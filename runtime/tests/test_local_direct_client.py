from types import SimpleNamespace

from fastapi.testclient import TestClient

from ai_bridge.protocol.result import ExecutionResult
from ai_bridge.transport.local_api import create_app


class _Stop:
    write_blocked = False


class _Sessions:
    def __init__(self):
        self.session = SimpleNamespace(
            session_id="HOU-LOCAL", adapter="houdini", adapter_version="0.5.26",
            host_version="21.0.440", project_file="E:/Test/Test.hip", pid=1234,
        )
    def list(self, adapter=None):
        if adapter and adapter != "houdini":
            return []
        return [self.session]
    def status(self, session_id):
        assert session_id == "HOU-LOCAL"
        return SimpleNamespace(state="connected")
    def is_active(self, session_id):
        return session_id == "HOU-LOCAL"


class _Workspaces:
    def list(self):
        return [SimpleNamespace(workspace_id="Bridge"), SimpleNamespace(workspace_id="Houdini")]


class _Bus:
    def set_timeout_handler(self, handler):
        pass
    def set_late_completion_handler(self, handler):
        pass


class _Service:
    def __init__(self):
        self.emergency_stop = _Stop()
        self.sessions = _Sessions()
        self.workspaces = _Workspaces()
        self.adapter_bus = _Bus()
        self.executed = []
    def active_sessions(self, adapter=None):
        return [item for item in self.sessions.list(adapter=adapter) if self.sessions.is_active(item.session_id)]
    def execute(self, command):
        self.executed.append(command)
        return ExecutionResult(command_id=command.command_id, status="success", result={"operation": command.operation})


def _client():
    service = _Service()
    return service, TestClient(create_app(service, auth_token="test-token"))


def _headers():
    return {"Authorization": "Bearer test-token"}


def _command(command_id="cmd-local"):
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Houdini",
        "adapter": "houdini",
        "session": "HOU-LOCAL",
        "project_file": "E:/Test/Test.hip",
        "operation": "session.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    }


def test_local_client_context_requires_auth_and_exposes_live_targeting():
    service, client = _client()
    assert client.get("/client/context").status_code == 401
    response = client.get("/client/context", headers=_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["protocol"] == "bridge-local-client/1"
    assert payload["workspaces"] == ["Bridge", "Houdini"]
    assert payload["sessions"][0]["session_id"] == "HOU-LOCAL"
    assert payload["sessions"][0]["project_file"] == "E:/Test/Test.hip"
    assert payload["targeting"]["implicit_session_selection"] is False


def test_local_client_accepts_bare_command_and_v5_style_command_wrapper():
    service, client = _client()
    first = client.post("/client/commands", headers=_headers(), json=_command("cmd-local-bare"))
    assert first.status_code == 200
    assert first.json()["command_id"] == "cmd-local-bare"
    wrapped = client.post(
        "/client/commands", headers=_headers(),
        json={"bridge_id": "ignored-transport-id", "channel_id": "local", "generation": "g1", "command": _command("cmd-local-wrapped")},
    )
    assert wrapped.status_code == 200
    assert wrapped.json()["command_id"] == "cmd-local-wrapped"
    assert [item.command_id for item in service.executed] == ["cmd-local-bare", "cmd-local-wrapped"]


def test_local_client_rejects_wrapper_predecessor_instead_of_ignoring_it():
    service, client = _client()
    response = client.post(
        "/client/commands", headers=_headers(),
        json={"command": _command(), "predecessor": {"command_id": "cmd-before", "require": "success"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "LOCAL_PREDECESSOR_UNSUPPORTED"
    assert service.executed == []


def test_legacy_commands_endpoint_remains_strict_bare_envelope():
    _, client = _client()
    response = client.post("/commands", headers=_headers(), json={"command": _command()})
    assert response.status_code == 422
