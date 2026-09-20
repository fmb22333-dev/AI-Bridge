from __future__ import annotations

from types import SimpleNamespace

import ai_bridge.core.host_process as host_process_module
from ai_bridge.core.host_process import HostProcessController


class FakeKernel32:
    def __init__(self, *, handle=101, exit_code=259, exit_query_ok=True):
        self.handle = handle
        self.exit_code = exit_code
        self.exit_query_ok = exit_query_ok
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, exit_code_ptr):
        if not self.exit_query_ok:
            return 0
        exit_code_ptr._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_windows_liveness_uses_exit_code_not_open_handle(monkeypatch):
    controller = HostProcessController()
    kernel32 = FakeKernel32(handle=123, exit_code=0)
    monkeypatch.setattr(
        host_process_module.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert controller._windows_process_is_running(444) is False
    assert kernel32.closed == [123]


def test_windows_liveness_reports_still_active(monkeypatch):
    controller = HostProcessController()
    kernel32 = FakeKernel32(handle=321, exit_code=259)
    monkeypatch.setattr(
        host_process_module.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert controller._windows_process_is_running(555) is True
    assert kernel32.closed == [321]


def test_windows_liveness_returns_false_when_process_handle_is_gone(monkeypatch):
    controller = HostProcessController()
    kernel32 = FakeKernel32(handle=0)
    monkeypatch.setattr(
        host_process_module.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert controller._windows_process_is_running(666) is False
    assert kernel32.closed == []


def test_terminate_is_idempotent_when_host_already_exited(monkeypatch):
    controller = HostProcessController()
    monkeypatch.setattr(controller, "is_running", lambda pid: False)

    def forbidden_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not execute for an already stopped host")

    monkeypatch.setattr(host_process_module.subprocess, "run", forbidden_run)

    result = controller.terminate(777)

    assert result["terminated"] is True
    assert result["already_stopped"] is True
