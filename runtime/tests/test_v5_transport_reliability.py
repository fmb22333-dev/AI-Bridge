import json

import httpx

import ai_bridge.transport.github_bus as gb
from ai_bridge.core.service import DuplicateCommandError
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


def _command(command_id="cmd-a"):
    return {"protocol": "bridge/1", "command_id": command_id, "workspace": "Bridge", "adapter": "bridge_admin", "operation": "bridge.update.status", "arguments": {}, "execution": {"verify": True, "checkpoint": "none", "dry_run": False}, "risk": "L1"}


def _v5_body(channel_id="chat-a", generation="gen-a", command=None):
    return gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps({"bridge_id": "bridge-test", "channel_id": channel_id, "generation": generation, "command": command or _command()})


def _transport(state, repo_items=None, issue_items=None):
    repo_items = repo_items if repo_items is not None else list(state)
    issue_items = issue_items if issue_items is not None else list(state)
    def item(cid):
        return {"id": cid, "issue_url": "https://api.github.com/repos/owner/repo/issues/1", "body": state[cid]}
    def handler(request):
        headers = {"ETag": '"v1"'}
        path = request.url.path
        if request.method == "GET" and path.endswith("/issues/1/comments"):
            return httpx.Response(200, headers=headers, json=[item(cid) for cid in issue_items])
        if request.method == "GET" and path.endswith("/issues/comments"):
            return httpx.Response(200, headers=headers, json=[item(cid) for cid in repo_items])
        if request.method == "GET" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=item(cid))
        if request.method == "PATCH":
            cid = int(path.rsplit("/", 1)[-1]); state[cid] = json.loads(request.content.decode())["body"]; return httpx.Response(200, headers=headers, json=item(cid))
        raise AssertionError(f"unexpected {request.method} {request.url}")
    return gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_v5_defers_ack_until_explicit_ack_after_durable_acceptance():
    state = {100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}), 201: _v5_body()}
    transport = _transport(state)
    assert transport.initialize_message_mode() == "issue_channel_v5"
    commands = transport.fetch_commands()
    assert [c.command_id for c in commands] == ["cmd-a"]
    assert state[201].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n")
    transport.ack_command(commands[0])
    assert state[201].startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n")
    assert json.loads(state[201].split("\n", 1)[1])["command"]["command_id"] == "cmd-a"


def test_v5_invalid_command_is_nacked_instead_of_silently_ignored():
    invalid = gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps({"bridge_id": "bridge-test", "channel_id": "chat-bad", "generation": "gen-bad", "command": {"protocol": "bridge/1", "command_id": "bad"}})
    state = {100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}), 201: invalid}
    transport = _transport(state); transport.initialize_message_mode()
    assert transport.fetch_commands() == []
    assert state[201].startswith("AI_BRIDGE_NACK_V5\n")
    assert json.loads(state[201].split("\n", 1)[1])["error"]["code"] == "INVALID_COMMAND_ENVELOPE"


def test_v5_saturated_repo_window_falls_back_to_issue_one_comments():
    mailbox = gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"})
    state = {100: mailbox, 500: _v5_body("chat-late", "gen-late", _command("cmd-late"))}; repo_state = {100: mailbox}
    for i in range(99):
        cid = 1000 + i; repo_state[cid] = "unrelated"; state[cid] = "unrelated"
    def repo_item(cid):
        issue = 1 if cid == 100 else 2; return {"id": cid, "issue_url": f"https://api.github.com/repos/owner/repo/issues/{issue}", "body": state[cid]}
    def issue_item(cid):
        return {"id": cid, "issue_url": "https://api.github.com/repos/owner/repo/issues/1", "body": state[cid]}
    def handler(request):
        headers = {"ETag": '"v1"'}; path = request.url.path
        if request.method == "GET" and path.endswith("/issues/1/comments"): return httpx.Response(200, headers=headers, json=[issue_item(100), issue_item(500)])
        if request.method == "GET" and path.endswith("/issues/comments"): return httpx.Response(200, headers=headers, json=[repo_item(cid) for cid in repo_state])
        if request.method == "GET" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=issue_item(cid))
        if request.method == "PATCH":
            cid = int(path.rsplit("/", 1)[-1]); state[cid] = json.loads(request.content.decode())["body"]; return httpx.Response(200, headers=headers, json=issue_item(cid))
        raise AssertionError(f"unexpected {request.method} {request.url}")
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); commands = transport.fetch_commands()
    assert [c.command_id for c in commands] == ["cmd-late"]
    assert state[500].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n")


def test_transport_accepted_db_row_is_claimable_by_service_execute(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db"); command = CommandEnvelope.model_validate(_command("cmd-db"))
    db.insert_command(command, status="transport_accepted")
    assert db.command_exists(command.command_id) is False
    db.insert_command(command)
    assert db.get_command(command.command_id)["status"] == "received"


class _RunnerDB:
    def __init__(self): self.rows = {}; self.published = []
    def is_published(self, key, command_id): return False
    def get_command(self, command_id): return self.rows.get(command_id)
    def insert_command(self, command, status="received"):
        existing = self.rows.get(command.command_id)
        if existing is None:
            self.rows[command.command_id] = {"command_id": command.command_id, "workspace_id": command.workspace, "adapter": command.adapter, "operation": command.operation, "status": status, "request": command.model_dump(mode="json")}; return
        if existing.get("status") == "transport_accepted" and status == "received": existing["status"] = "received"; return
        raise RuntimeError("duplicate")
    def command_exists(self, command_id):
        row = self.rows.get(command_id); return bool(row and row.get("status") != "transport_accepted")
    def save_result(self, result):
        row = self.rows[result.command_id]; row["status"] = result.status.value; row["result"] = result.model_dump(mode="json")
    def mark_published(self, key, command_id): self.published.append((key, command_id))


class _RunnerService:
    def __init__(self, db): self.db = db; self.execute_calls = 0
    def execute(self, command):
        self.execute_calls += 1; assert self.db.rows[command.command_id]["status"] == "transport_accepted"; self.db.insert_command(command)
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={"ok": True})


class _RunnerTransport:
    key = "test:v5"
    def __init__(self, command, db): self.command = command; self.db = db; self.acked = []; self.results = []
    def fetch_commands(self): return [self.command]
    def ack_command(self, command): self.acked.append(command.command_id)
    def publish_result(self, result): self.results.append(result)


def test_runner_persists_before_ack_then_executes_and_publishes():
    command = CommandEnvelope.model_validate(_command("cmd-runner")); db = _RunnerDB(); service = _RunnerService(db); transport = _RunnerTransport(command, db)
    assert TransportRunner(service, transport).poll_once() == 1
    assert transport.acked == ["cmd-runner"]; assert service.execute_calls == 1; assert transport.results[0].status == ExecutionStatus.SUCCESS; assert db.published == [("test:v5", "cmd-runner")]


def test_runner_republishes_terminal_row_without_reexecution():
    command = CommandEnvelope.model_validate(_command("cmd-terminal")); db = _RunnerDB(); terminal = ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={"value": 42})
    db.rows[command.command_id] = {"command_id": command.command_id, "workspace_id": command.workspace, "adapter": command.adapter, "operation": command.operation, "status": "success", "request": command.model_dump(mode="json"), "result": terminal.model_dump(mode="json")}
    class Service(_RunnerService):
        def execute(self, command): raise DuplicateCommandError(command.command_id)
    service = Service(db); transport = _RunnerTransport(command, db)
    assert TransportRunner(service, transport).poll_once() == 1
    assert transport.acked == ["cmd-terminal"]; assert transport.results[0].result["value"] == 42
