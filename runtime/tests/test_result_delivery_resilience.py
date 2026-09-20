from __future__ import annotations

from types import SimpleNamespace

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.result_delivery import prepare_result_delivery
from ai_bridge.transport.runner import TransportRunner


class FakeTransport:
    key = "test:v5"

    def __init__(self):
        self.config = SimpleNamespace(bridge_id="bridge-test")
        self._comment_refs = {
            "cmd-large": {
                "mode": "issue_channel_v5",
                "channel_id": "chat-a",
                "generation": "gen-a",
                "command": {"old": "full"},
            }
        }
        self.writes = []
        self.fail_full_archive = False
        self.published = None
        self.commands = []
        self.remembered = []

    def _bus_path(self, path):
        return ".ai-bridge/" + path

    def _publish_json(self, path, payload, **kwargs):
        if self.fail_full_archive:
            raise RuntimeError("archive unavailable")
        self.writes.append((path, payload, kwargs))
        return "sha"

    def remember_full_result(self, result):
        self.remembered.append(result.model_copy(deep=True))

    def fetch_commands(self):
        return list(self.commands)

    def publish_result(self, result):
        self.published = result


def _large_command():
    return CommandEnvelope(
        command_id="cmd-large",
        workspace="Houdini",
        adapter="houdini",
        session="HOU-1",
        project_file="E:/Test/Test.hip",
        operation="inspect.node",
        arguments={"path": "/obj/test", "payload": "x" * 12000},
    )


def test_small_result_stays_on_fast_comment_path_without_contents_write():
    transport = FakeTransport()
    command = CommandEnvelope(
        command_id="cmd-large",
        workspace="Bridge",
        adapter="bridge_admin",
        operation="bridge.update.status",
    )
    result = ExecutionResult(
        command_id="cmd-large",
        status=ExecutionStatus.SUCCESS,
        result={"ok": True},
    )
    delivered = prepare_result_delivery(transport, command, result)
    assert delivered is result
    assert transport.writes == []
    assert transport.remembered == []
    assert transport._comment_refs["cmd-large"]["command"] == {"old": "full"}


def test_large_envelope_compacts_without_contents_write_and_remembers_full_result():
    transport = FakeTransport()
    command = _large_command()
    result = ExecutionResult(
        command_id="cmd-large",
        status=ExecutionStatus.SUCCESS,
        result={"path": "/obj/test", "huge": "z" * 20000},
    )
    compact = prepare_result_delivery(transport, command, result)

    assert transport.writes == []
    assert len(transport.remembered) == 1
    assert transport.remembered[0].result["huge"] == "z" * 20000
    assert compact.result["path"] == "/obj/test"
    assert compact.result["huge"]["_truncated"] is True
    delivery = compact.result["_bridge"]["delivery"]
    assert delivery["mode"] == "structured_summary"
    assert delivery["full_result_source"] == "local_command_result"
    assert delivery["recovery"]["arguments"]["command_id"] == "cmd-large"
    assert "$.huge" in delivery["truncated_paths"]
    assert transport._comment_refs["cmd-large"]["command"]["argument_keys"] == ["path", "payload"]


def test_structured_compaction_does_not_depend_on_contents_archive_availability():
    transport = FakeTransport()
    transport.fail_full_archive = True
    result = ExecutionResult(
        command_id="cmd-large",
        status=ExecutionStatus.SUCCESS,
        result={"huge": "z" * 20000},
    )

    delivered = prepare_result_delivery(transport, _large_command(), result)

    assert delivered is not result
    assert delivered.result["huge"]["_truncated"] is True
    assert transport.writes == []
    assert transport.remembered[0].result["huge"] == "z" * 20000


class StatusDB:
    def __init__(self, row):
        self.row = row
        self.marked = None

    def is_published(self, key, command_id):
        return False

    def get_command(self, command_id):
        return self.row if command_id == "cmd-target" else None

    def mark_published(self, key, command_id):
        self.marked = (key, command_id)


class StatusService:
    def __init__(self, row):
        self.db = StatusDB(row)

    def execute(self, command):
        raise AssertionError("status query must not enter BridgeService.execute")


def test_transport_status_query_recovers_terminal_command_without_reexecution():
    row = {
        "command_id": "cmd-target",
        "workspace_id": "Houdini",
        "adapter": "houdini",
        "operation": "cook.execute",
        "status": "success",
        "evidence_id": "ev-target",
        "created_at": "2026-09-08 00:00:00",
        "updated_at": "2026-09-08 00:00:01",
        "result": {
            "command_id": "cmd-target",
            "status": "success",
            "result": {"value": 42},
        },
    }
    service = StatusService(row)
    transport = FakeTransport()
    transport._comment_refs = {}
    transport.commands = [
        CommandEnvelope(
            command_id="cmd-query",
            workspace="Bridge",
            adapter="bridge_transport",
            operation="command.status",
            arguments={"command_id": "cmd-target", "include_result": True},
        )
    ]
    assert TransportRunner(service, transport).poll_once() == 1
    result = transport.published
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["target_command_id"] == "cmd-target"
    assert result.result["state"] == "success"
    assert result.result["terminal"] is True
    assert result.result["terminal_result"]["result"]["value"] == 42
    assert service.db.marked == ("test:v5", "cmd-query")
