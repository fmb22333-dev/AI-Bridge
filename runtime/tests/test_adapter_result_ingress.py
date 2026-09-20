from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.adapter_bus import AdapterCommandBus
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, FailureInfo
from ai_bridge.transport.local_api import create_app


class FakeSessions:
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


class FakeBus:
    def __init__(self):
        self.completed = []

    def complete(self, result):
        self.completed.append(result)
        return True

    def poll(self, session_id, timeout=0.0):
        return None


class FakeStop:
    write_blocked = False


class FakeService:
    def __init__(self):
        self.sessions = FakeSessions()
        self.adapter_bus = FakeBus()
        self.emergency_stop = FakeStop()


def _headers():
    return {"Authorization": "Bearer test-token"}


def _base_result():
    return {
        "command_id": "cmd-failure",
        "status": "failed",
        "stages": {"EXECUTE": "FAILED"},
        "result": {},
        "failure": {
            "origin": "host",
            "stage": "execute",
            "code": "ARGUMENT_REQUIRED",
            "message": "ARGUMENT_REQUIRED: recipe",
            "category": "invalid_argument",
            "exception_type": "ValueError",
            "retryable": False,
            "suggestion": "Provide the required operation argument and retry.",
            "knowledge_rule": "ARGUMENT_REQUIRED",
            "context": {"operation": "recipe.get"},
            "host_errors": [],
            "host_warnings": [],
            "supported_operations": [],
            "underlying": {"source": "adapter"},
        },
        "rollback_available": False,
        "last_known_state": {"host": "alive"},
        "evidence_id": None,
    }


def test_failure_info_accepts_structured_adapter_diagnostics():
    failure = FailureInfo.model_validate(_base_result()["failure"])
    assert failure.code == "ARGUMENT_REQUIRED"
    assert failure.category == "invalid_argument"
    assert failure.context["operation"] == "recipe.get"
    assert failure.retryable is False


def test_failure_info_is_forward_compatible_for_future_diagnostics():
    payload = dict(_base_result()["failure"])
    payload["future_diagnostic"] = {"revision": 2}
    failure = FailureInfo.model_validate(payload)
    assert failure.model_dump()["future_diagnostic"] == {"revision": 2}


def test_adapter_poll_refreshes_transport_liveness():
    service = FakeService()
    client = TestClient(create_app(service, auth_token="test-token"))

    response = client.get(
        "/adapter/poll/HOU-TEST?timeout=0",
        headers=_headers(),
    )

    assert response.status_code == 204
    assert service.sessions.touches == ["HOU-TEST"]


def test_adapter_result_accepts_structured_failure_without_422():
    service = FakeService()
    client = TestClient(create_app(service, auth_token="test-token"))

    response = client.post(
        "/adapter/result/HOU-TEST",
        headers=_headers(),
        json=_base_result(),
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert service.sessions.touches == ["HOU-TEST"]
    result = service.adapter_bus.completed[-1]
    assert isinstance(result, ExecutionResult)
    assert result.failure.code == "ARGUMENT_REQUIRED"
    assert result.failure.category == "invalid_argument"


def test_adapter_bus_consumes_stale_result_idempotently():
    bus = AdapterCommandBus()
    stale = ExecutionResult(
        command_id="cmd-from-pre-restart-runtime",
        status="failed",
        failure={"origin": "adapter", "code": "ADAPTER_TIMEOUT"},
    )
    assert bus.complete(stale) is True


def test_adapter_bus_duplicate_result_does_not_overwrite_terminal_result():
    bus = AdapterCommandBus()
    command = CommandEnvelope(
        command_id="cmd-dupe",
        workspace="Bridge",
        adapter="houdini",
        operation="inspect.context",
        arguments={},
    )
    bus.ensure_session("HOU-TEST")
    from ai_bridge.core.adapter_bus import PendingResult
    from threading import Event
    first = ExecutionResult(command_id="cmd-dupe", status="success", result={"value": 1})
    second = ExecutionResult(command_id="cmd-dupe", status="failed", failure={"origin": "adapter", "code": "LATE"})
    pending = PendingResult(command=command, event=Event())
    bus._pending["cmd-dupe"] = pending

    assert bus.complete(first) is True
    assert bus.complete(second) is True
    assert pending.result.result == {"value": 1}
    assert pending.result.status.value == "success"
