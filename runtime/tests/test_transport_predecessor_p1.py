from __future__ import annotations

import json

import httpx

import ai_bridge.transport
import ai_bridge.transport.github_bus as gb
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


class PredDB:
    def __init__(self, predecessor_row=None):
        self.rows = {}
        if predecessor_row is not None:
            self.rows["cmd-prev"] = predecessor_row
        self.published = []

    def is_published(self, key, command_id):
        return False

    def get_command(self, command_id):
        return self.rows.get(command_id)

    def accept_transport_command(self, command):
        row = self.rows.get(command.command_id)
        if row is None:
            row = {
                "command_id": command.command_id,
                "workspace_id": command.workspace,
                "adapter": command.adapter,
                "operation": command.operation,
                "status": "transport_accepted",
                "result": None,
            }
            self.rows[command.command_id] = row
        return row

    def save_result(self, result):
        row = self.rows.setdefault(result.command_id, {})
        row["status"] = result.status.value
        row["result"] = result.model_dump(mode="json")

    def mark_published(self, key, command_id):
        self.published.append((key, command_id))


class PredService:
    def __init__(self, db):
        self.db = db

    def execute(self, command):
        raise AssertionError("transport.ping must stay on control plane")


class PredTransport:
    key = "test:pred"

    def __init__(self, predecessor):
        self.commands = [
            CommandEnvelope(
                command_id="cmd-next",
                workspace="Bridge",
                adapter="bridge_transport",
                operation="transport.ping",
                execution={"verify": True, "checkpoint": "none", "dry_run": False},
                risk="L1",
            )
        ]
        self.predecessor = predecessor
        self.acks = []
        self.nacks = []
        self.results = []

    def fetch_commands(self):
        commands, self.commands = self.commands, []
        return commands

    def requires_durable_ack(self, command):
        return True

    def command_predecessor(self, command_id):
        return self.predecessor if command_id == "cmd-next" else None

    def nack_command(self, command, code, message):
        self.nacks.append((command.command_id, code, message))

    def ack_command(self, command):
        self.acks.append(command.command_id)

    def publish_result(self, result):
        self.results.append(result)


def _run_predecessor(predecessor_row, require="success"):
    db = PredDB(predecessor_row)
    transport = PredTransport({"command_id": "cmd-prev", "require": require})
    runner = TransportRunner(PredService(db), transport)
    try:
        count = runner.poll_once()
    finally:
        runner.shutdown(wait=True)
    return count, db, transport


def test_missing_predecessor_nacks_before_ack_and_accept():
    count, db, transport = _run_predecessor(None)
    assert count == 1
    assert transport.acks == []
    assert transport.nacks and transport.nacks[0][1] == "PREDECESSOR_NOT_FOUND"
    assert "cmd-next" not in db.rows


def test_nonterminal_predecessor_nacks_before_ack():
    row = {"command_id": "cmd-prev", "status": "transport_accepted", "result": None}
    _, db, transport = _run_predecessor(row)
    assert transport.acks == []
    assert transport.nacks[0][1] == "PREDECESSOR_NOT_TERMINAL"
    assert "cmd-next" not in db.rows


def test_failed_predecessor_nacks_when_success_required():
    row = {
        "command_id": "cmd-prev",
        "status": "failed",
        "result": {"command_id": "cmd-prev", "status": "failed", "result": {}},
    }
    _, db, transport = _run_predecessor(row, require="success")
    assert transport.acks == []
    assert transport.nacks[0][1] == "PREDECESSOR_NOT_SUCCESS"
    assert "cmd-next" not in db.rows


def test_terminal_requirement_allows_failed_terminal_predecessor():
    row = {
        "command_id": "cmd-prev",
        "status": "failed",
        "result": {"command_id": "cmd-prev", "status": "failed", "result": {}},
    }
    count, _, transport = _run_predecessor(row, require="terminal")
    assert count == 1
    assert transport.nacks == []
    assert transport.acks == ["cmd-next"]
    assert transport.results[-1].status == ExecutionStatus.SUCCESS


def test_success_requirement_allows_successful_predecessor():
    row = {
        "command_id": "cmd-prev",
        "status": "success",
        "result": {"command_id": "cmd-prev", "status": "success", "result": {}},
    }
    count, _, transport = _run_predecessor(row, require="success")
    assert count == 1
    assert transport.nacks == []
    assert transport.acks == ["cmd-next"]
    assert transport.results[-1].result["state"] == "online"


def test_invalid_v5_predecessor_schema_nacks_at_ingress():
    command = {
        "protocol": "bridge/1",
        "command_id": "cmd-invalid-pred",
        "workspace": "Bridge",
        "adapter": "bridge_transport",
        "operation": "transport.ping",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    }
    state = {
        100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}),
        201: gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps({
            "bridge_id": "bridge-test",
            "channel_id": "chat-invalid-pred",
            "generation": "gen-invalid-pred",
            "predecessor": {"command_id": "cmd-prev", "require": "eventually"},
            "command": command,
        }),
    }

    def item(comment_id):
        return {
            "id": comment_id,
            "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
            "body": state[comment_id],
        }

    def handler(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("/issues/comments"):
            return httpx.Response(200, json=[item(cid) for cid in state])
        if request.method == "GET" and path.endswith("/issues/1/comments"):
            return httpx.Response(200, json=[item(cid) for cid in state])
        if request.method == "GET" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=item(cid))
        if request.method == "PATCH" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1])
            state[cid] = json.loads(request.content.decode())["body"]
            return httpx.Response(200, json=item(cid))
        if request.method == "GET" and "/contents/.ai-bridge/commands/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = gb.GitHubBusTransport(
        gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transport.initialize_message_mode()
    assert transport.fetch_commands() == []
    assert state[201].startswith("AI_BRIDGE_NACK_V5\n")
    payload = json.loads(state[201].split("\n", 1)[1])
    assert payload["error"]["code"] == "INVALID_PREDECESSOR"
