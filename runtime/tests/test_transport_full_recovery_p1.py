from __future__ import annotations

from types import SimpleNamespace

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.result_delivery import prepare_result_delivery


class RecoveryTransport:
    def __init__(self):
        self.config = SimpleNamespace(bridge_id="bridge-test")
        self._comment_refs = {
            "cmd-status": {
                "mode": "issue_channel_v5",
                "channel_id": "chat-recovery",
                "generation": "gen-recovery",
                "command": {"old": "full"},
            }
        }
        self.writes = []
        self.remembered = []

    def _bus_path(self, path):
        return ".ai-bridge/" + path

    def _publish_json(self, path, payload, **kwargs):
        self.writes.append((path, payload, kwargs))
        return "sha"

    def remember_full_result(self, result):
        self.remembered.append(result.model_copy(deep=True))


def _status_command():
    return CommandEnvelope(
        command_id="cmd-status",
        workspace="Bridge",
        adapter="bridge_transport",
        operation="command.status",
        arguments={"command_id": "cmd-target", "include_result": True},
        execution={"verify": True, "checkpoint": "none", "dry_run": False},
        risk="L1",
    )


def _status_result(chars):
    return ExecutionResult(
        command_id="cmd-status",
        status=ExecutionStatus.SUCCESS,
        result={
            "target_command_id": "cmd-target",
            "state": "success",
            "terminal": True,
            "terminal_result": {
                "command_id": "cmd-target",
                "status": "success",
                "result": {"payload": "x" * chars, "count": chars},
            },
        },
    )


def test_include_result_recovery_under_comment_limit_stays_full():
    transport = RecoveryTransport()
    original = _status_result(20000)
    delivered = prepare_result_delivery(transport, _status_command(), original)
    assert delivered is original
    assert delivered.result["terminal_result"]["result"]["payload"] == "x" * 20000
    assert transport.writes == []
    assert transport.remembered == []


def test_include_result_recovery_above_comment_limit_archives_only_on_demand():
    transport = RecoveryTransport()
    original = _status_result(70000)
    delivered = prepare_result_delivery(transport, _status_command(), original)
    assert len(transport.writes) == 1
    path, archived, _ = transport.writes[0]
    assert path.endswith("/channels/chat-recovery/cmd-status.json")
    assert archived["result"]["result"]["terminal_result"]["result"]["payload"] == "x" * 70000
    delivery = delivered.result["_bridge"]["delivery"]
    assert delivery["mode"] == "full_reference"
    assert delivery["full_result_source"] == "github_contents_on_demand"
    assert delivery["full_result_ref"] == path
    assert delivery["full_result_chars"] > 50000
    assert delivered.result["target_command_id"] == "cmd-target"
    assert delivered.result["state"] == "success"
    assert delivered.result["terminal"] is True
    assert "terminal_result" not in delivered.result
