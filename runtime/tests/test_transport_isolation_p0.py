from __future__ import annotations

import threading
import time

import ai_bridge.transport.remote_controller as remote_controller
from ai_bridge.core.adapter_bus import AdapterCommandBus
from ai_bridge.core.sessions import SessionInfo, SessionRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


class _DB:
    def __init__(self):
        self.rows = {}
        self.published = set()

    def is_published(self, transport_key, command_id):
        return (transport_key, command_id) in self.published

    def mark_published(self, transport_key, command_id):
        self.published.add((transport_key, command_id))

    def save_result(self, result):
        row = self.rows.setdefault(result.command_id, {})
        row["result"] = result.model_dump(mode="json")
        row["status"] = result.status.value

    def get_command(self, command_id):
        return self.rows.get(command_id)


class _Service:
    def __init__(self, execute):
        self.db = _DB()
        self._execute = execute

    def execute(self, command):
        return self._execute(command)


class _Transport:
    key = "test-transport"

    def __init__(self, batches):
        self.batches = list(batches)
        self.published = []

    def fetch_commands(self):
        return self.batches.pop(0) if self.batches else []

    def publish_result(self, result):
        self.published.append(result)

    def message_state(self):
        return {"mode": "issue_channel_v5", "multi_channel": True}


def _cmd(command_id, *, session=None, adapter="houdini", operation="hip.status", workspace=None):
    return CommandEnvelope(
        command_id=command_id,
        workspace=workspace or ("Houdini" if session else "Bridge"),
        adapter=adapter,
        session=session,
        project_file="E:/Test/test.hip" if session else None,
        operation=operation,
        arguments={},
        risk="L1",
    )


def test_transport_runner_dispatches_different_session_lanes_without_global_head_of_line_blocking():
    slow_started = threading.Event()
    release_slow = threading.Event()
    fast_done = threading.Event()

    def execute(command):
        if command.command_id == "slow":
            slow_started.set()
            release_slow.wait(0.6)
        else:
            fast_done.set()
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS)

    transport = _Transport([[
        _cmd("slow", session="HOU-A"),
        _cmd("fast", session="HOU-B"),
    ]])
    runner = TransportRunner(_Service(execute), transport)
    started = time.perf_counter()
    accepted = runner.poll_once()
    elapsed = time.perf_counter() - started
    try:
        assert accepted == 2
        assert elapsed < 0.20
        assert slow_started.wait(0.20)
        assert fast_done.wait(0.20)
    finally:
        release_slow.set()
        shutdown = getattr(runner, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=True)


def test_transport_runner_dispatches_different_workspace_lanes_without_head_of_line_blocking():
    slow_started = threading.Event()
    release_slow = threading.Event()
    fast_done = threading.Event()

    def execute(command):
        if command.command_id == "workspace-slow":
            slow_started.set()
            release_slow.wait(0.6)
        else:
            fast_done.set()
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS)

    transport = _Transport([[
        _cmd("workspace-slow", adapter="workspace", operation="workspace.command.run", workspace="AI_拟核"),
        _cmd("workspace-fast", adapter="workspace", operation="workspace.files.read", workspace="UE58"),
    ]])
    runner = TransportRunner(_Service(execute), transport)
    try:
        assert runner.poll_once() == 2
        assert slow_started.wait(0.20)
        assert fast_done.wait(0.20)
    finally:
        release_slow.set()
        runner.shutdown(wait=True)


def test_transport_runner_serializes_same_workspace_lane():
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def execute(command):
        if command.command_id == "workspace-same-1":
            first_started.set()
            release_first.wait(0.6)
        else:
            second_started.set()
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS)

    transport = _Transport([[
        _cmd("workspace-same-1", adapter="workspace", operation="workspace.command.run", workspace="AI_拟核"),
        _cmd("workspace-same-2", adapter="workspace", operation="workspace.files.read", workspace="AI_拟核"),
    ]])
    runner = TransportRunner(_Service(execute), transport)
    try:
        assert runner.poll_once() == 2
        assert first_started.wait(0.20)
        assert not second_started.wait(0.10)
        release_first.set()
        assert second_started.wait(0.30)
    finally:
        release_first.set()
        runner.shutdown(wait=True)


def test_transport_ping_is_local_control_plane_and_does_not_call_service_execute():
    def execute(command):
        raise AssertionError("transport.ping must not enter service.execute")

    command = _cmd(
        "ping-1",
        adapter="bridge_transport",
        operation="transport.ping",
    )
    transport = _Transport([[command]])
    runner = TransportRunner(_Service(execute), transport)
    assert runner.poll_once() == 1
    assert len(transport.published) == 1
    result = transport.published[0]
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["state"] == "online"
    assert result.result["control_plane"] is True
    assert result.result["transport_key"] == transport.key


def test_github_auth_failure_classification_and_backoff_are_explicit_and_bounded():
    classify = getattr(remote_controller, "_classify_github_transport_error", None)
    retry_delay = getattr(remote_controller, "_auth_retry_delay", None)
    assert callable(classify)
    assert callable(retry_delay)
    assert classify(RuntimeError('GitHub HTTP 401: {\"message\": \"Bad credentials\"}')) == "auth_degraded"
    assert classify(RuntimeError("GitHub HTTP 429: rate limit")) == "rate_limited"
    assert 1.0 <= retry_delay(1) < retry_delay(2) <= retry_delay(20) <= 120.0


def test_busy_unknown_survives_heartbeat_and_only_matching_late_result_clears_it():
    registry = SessionRegistry(stale_after_seconds=30.0)
    info = SessionInfo(
        session_id="HOU-BUSY",
        adapter="houdini",
        adapter_version="0.5.26",
        host_version="21.0.440",
        pid=1234,
        project_file="E:/Test/test.hip",
    )
    registry.register(info)
    registry.mark_busy_unknown("HOU-BUSY", command_id="slow-cmd", operation="cook.execute")
    registry.heartbeat("HOU-BUSY", project_file=info.project_file)
    assert registry.status("HOU-BUSY").state == "busy_unknown"
    assert registry.clear_busy_unknown("HOU-BUSY", command_id="other-cmd") is False
    assert registry.status("HOU-BUSY").state == "busy_unknown"
    assert registry.clear_busy_unknown("HOU-BUSY", command_id="slow-cmd") is True
    assert registry.status("HOU-BUSY").state == "connected"


def test_adapter_bus_reports_exact_late_completion_after_timeout():
    observed = []
    bus = AdapterCommandBus()
    bus.set_late_completion_handler(
        lambda session_id, command_id, result: observed.append((session_id, command_id, result.status.value))
    )
    command = _cmd("late-cmd", session="HOU-LATE")
    assert bus.submit("HOU-LATE", command, timeout=0.01) is None
    result = ExecutionResult(command_id="late-cmd", status=ExecutionStatus.SUCCESS)
    assert bus.complete(result) is True
    assert observed == [("HOU-LATE", "late-cmd", "success")]

def test_transport_runner_serializes_same_session_lane():
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def execute(command):
        if command.command_id == "same-1":
            first_started.set()
            release_first.wait(0.6)
        else:
            second_started.set()
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS)

    transport = _Transport([[
        _cmd("same-1", session="HOU-SAME"),
        _cmd("same-2", session="HOU-SAME"),
    ]])
    runner = TransportRunner(_Service(execute), transport)
    try:
        assert runner.poll_once() == 2
        assert first_started.wait(0.20)
        assert not second_started.wait(0.10)
        release_first.set()
        assert second_started.wait(0.30)
    finally:
        release_first.set()
        runner.shutdown(wait=True)


def test_queue_executor_blocks_busy_unknown_until_exact_late_result_arrives():
    from ai_bridge.core.remote_adapter import QueueAdapterExecutor

    bus = AdapterCommandBus()
    executor = QueueAdapterExecutor(bus)
    first = _cmd("budget-timeout", session="HOU-BLOCK")
    timed_out = executor.execute(first, timeout=0.01)
    assert timed_out.failure.code == "EXECUTION_BUDGET_EXCEEDED"
    blocked = executor.execute(_cmd("blocked-next", session="HOU-BLOCK"), timeout=0.01)
    assert blocked.status == ExecutionStatus.CONFLICT
    assert blocked.failure.code == "SESSION_BUSY_UNKNOWN"
    bus.complete(ExecutionResult(command_id="budget-timeout", status=ExecutionStatus.SUCCESS))
    assert bus.busy_source("HOU-BLOCK") is None


def test_transport_runner_fetches_live_control_plane_before_pending_receipt_retry():
    order = []

    class OrderingDB(_DB):
        def list_pending_terminal_receipts(self, transport_key, limit=100):
            order.append("retry")
            return []

        def mark_ingress_receipt_published(self, transport_key, receipt_id):
            pass

    class OrderingTransport(_Transport):
        def fetch_commands(self):
            order.append("fetch")
            return super().fetch_commands()

        def publish_result_for_receipt(self, result, receipt):
            pass

    command = _cmd(
        "priority-ping",
        adapter="bridge_transport",
        operation="transport.ping",
    )
    service = _Service(lambda command: (_ for _ in ()).throw(AssertionError("ping must remain local")))
    service.db = OrderingDB()
    transport = OrderingTransport([[command]])
    runner = TransportRunner(service, transport)

    assert runner.poll_once() == 1
    assert order == ["fetch"]
    runner.shutdown()
