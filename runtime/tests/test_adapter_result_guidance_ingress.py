from fastapi.testclient import TestClient

from ai_bridge.protocol.result import ExecutionResult
from ai_bridge.transport.local_api import create_app


class _Sessions:
    def __init__(self):
        self.touches = []
    def get(self, session_id):
        if session_id != "HOU-TEST":
            raise KeyError(session_id)
        return object()
    def touch(self, session_id):
        value = self.get(session_id)
        self.touches.append(session_id)
        return value
    def list(self):
        return []


class _Bus:
    def __init__(self):
        self.completed = []
    def complete(self, result):
        self.completed.append(result)
        return True
    def poll(self, session_id, timeout=0.0):
        return None


class _Stop:
    write_blocked = False


class _Service:
    def __init__(self):
        self.sessions = _Sessions()
        self.adapter_bus = _Bus()
        self.emergency_stop = _Stop()


def _client():
    service = _Service()
    return service, TestClient(create_app(service, auth_token="test-token"))


def _post(client, payload):
    return client.post("/adapter/result/HOU-TEST", headers={"Authorization": "Bearer test-token"}, json=payload)


def _base(status="success"):
    return {
        "command_id": "cmd-guidance",
        "status": status,
        "stages": {"READBACK": "VERIFIED"},
        "result": {"value": 1},
        "failure": None,
        "rollback_available": False,
        "last_known_state": {"host": "alive"},
        "evidence_id": None,
    }


def test_adapter_result_accepts_advisory_guidance_without_422():
    service, client = _client()
    payload = _base()
    payload["guidance"] = {"mode": "advisory_only", "blocking": False, "suggestions": [{"id": "example"}]}
    response = _post(client, payload)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    result = service.adapter_bus.completed[-1]
    assert isinstance(result, ExecutionResult)
    assert result.guidance["mode"] == "advisory_only"


def test_adapter_result_accepts_skipped_status_without_422():
    service, client = _client()
    payload = _base("skipped")
    response = _post(client, payload)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert service.adapter_bus.completed[-1].status.value == "skipped"


def test_invalid_adapter_result_becomes_terminal_failure_instead_of_422_loop():
    service, client = _client()
    payload = _base()
    payload["future_unknown_top_level"] = {"revision": 2}
    response = _post(client, payload)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["normalized_invalid"] is True
    result = service.adapter_bus.completed[-1]
    assert result.command_id == "cmd-guidance"
    assert result.status.value == "failed"
    assert result.failure.code == "ADAPTER_RESULT_SCHEMA_INVALID"
    assert result.failure.retryable is False
