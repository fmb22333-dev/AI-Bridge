import sys
import time

import pytest

from ai_bridge.core.worker_manifest import ServiceSpec
from ai_bridge.core.worker_supervisor import (
    WorkerAlreadyRunning,
    WorkerNotRunning,
    WorkerReloadManual,
    WorkerSupervisor,
)


def _service(*, reload="restart", text="ready"):
    return ServiceSpec(
        argv=[
            sys.executable,
            "-S",
            "-u",
            "-c",
            f"import time; print({text!r}, flush=True); time.sleep(30)",
        ],
        reload=reload,
        stop_timeout_seconds=1,
    )


def _wait_for_log(supervisor, workspace_id, name, needle):
    deadline = time.time() + 3
    while time.time() < deadline:
        payload = supervisor.logs(workspace_id, name, tail=20)
        if any(needle in line for line in payload["lines"]):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"log not observed: {needle}")


def test_supervisor_start_status_logs_restart_and_stop(tmp_path):
    supervisor = WorkerSupervisor(max_log_lines=64)
    spec = _service()

    started = supervisor.start("ws", tmp_path, "bot", spec)
    assert started["state"] == "running"
    first_pid = started["pid"]
    _wait_for_log(supervisor, "ws", "bot", "ready")

    with pytest.raises(WorkerAlreadyRunning):
        supervisor.start("ws", tmp_path, "bot", spec)

    restarted = supervisor.restart("ws", tmp_path, "bot", spec)
    assert restarted["state"] == "running"
    assert restarted["pid"] != first_pid

    stopped = supervisor.stop("ws", "bot")
    assert stopped["state"] == "stopped"
    assert supervisor.status("ws", "bot")["state"] == "stopped"
    with pytest.raises(WorkerNotRunning):
        supervisor.stop("ws", "bot")

    supervisor.close()


def test_supervisor_reload_modes(tmp_path):
    supervisor = WorkerSupervisor(max_log_lines=32)

    native = _service(reload="native", text="native")
    started = supervisor.start("ws", tmp_path, "web", native)
    native_result = supervisor.reload("ws", tmp_path, "web", native)
    assert native_result["action"] == "native"
    assert native_result["pid"] == started["pid"]

    restart = _service(reload="restart", text="restart")
    restarted = supervisor.reload("ws", tmp_path, "web", restart)
    assert restarted["action"] == "restart"
    assert restarted["pid"] != started["pid"]

    with pytest.raises(WorkerReloadManual):
        supervisor.reload("ws", tmp_path, "manual", _service(reload="manual"))

    supervisor.close()


def test_native_reload_requires_running_service(tmp_path):
    supervisor = WorkerSupervisor(max_log_lines=32)

    with pytest.raises(WorkerNotRunning):
        supervisor.reload("ws", tmp_path, "web", _service(reload="native"))


def test_windows_tree_stop_uses_taskkill(monkeypatch):
    calls = []

    class Result:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    monkeypatch.setattr("ai_bridge.core.worker_supervisor.subprocess.run", fake_run)

    result = WorkerSupervisor._windows_taskkill(1234, force=False)
    assert result is True
    assert calls[0][0] == ["taskkill", "/PID", "1234", "/T"]

    result = WorkerSupervisor._windows_taskkill(1234, force=True)
    assert result is True
    assert calls[1][0] == ["taskkill", "/PID", "1234", "/T", "/F"]
