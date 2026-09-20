from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import client
from ai_bridge_houdini.client import HoudiniBridgeRuntime, UI_TICK_STALE_SECONDS


class FakeHipFile:
    def __init__(self):
        self.calls = 0

    def path(self):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("transient hip path failure")
        return "E:/AA/TestLiveness.hip"


class FakeHou:
    def __init__(self):
        self.hipFile = FakeHipFile()

    def applicationVersionString(self):
        return "21.0.440"


def _runtime():
    return HoudiniBridgeRuntime(
        FakeHou(),
        config={"url": "http://127.0.0.1:9", "token": "test"},
    )


def _command(command_id="cmd-live"):
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "houdini",
        "operation": "adapter.capabilities",
        "session": "HOU-TEST",
        "project_file": "E:/AA/TestLiveness.hip",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    }


def _success(command):
    return {
        "command_id": command["command_id"],
        "status": "success",
        "stages": {},
        "result": {"ok": True},
        "failure": None,
        "rollback_available": False,
        "last_known_state": {},
        "evidence_id": None,
    }


def test_tick_survives_transient_project_path_failure(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(client, "dispatch", lambda hou, command, session: _success(command))
    runtime._enqueue_ui_command(_command("cmd-a"))

    runtime.tick()

    result = runtime.outgoing.get_nowait()
    assert result["command_id"] == "cmd-a"
    assert result["status"] == "success"
    assert runtime.last_ui_tick_error is not None
    assert runtime.last_ui_tick_error["exception_type"] == "RuntimeError"

    runtime._enqueue_ui_command(_command("cmd-b"))
    runtime.tick()
    assert runtime.outgoing.get_nowait()["command_id"] == "cmd-b"


def test_stalled_ui_command_fails_fast_and_is_not_dispatched(monkeypatch):
    runtime = _runtime()
    called = []
    monkeypatch.setattr(client, "dispatch", lambda *args, **kwargs: called.append(True))
    now = time.monotonic()
    runtime._enqueue_ui_command(_command("cmd-stalled"), now=now - 10.0)

    assert runtime._expire_stalled_ui_commands(now=now) == 1
    result = runtime.outgoing.get_nowait()
    assert result["command_id"] == "cmd-stalled"
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "ADAPTER_UI_PUMP_STALLED"

    runtime.tick()
    assert called == []
    with pytest.raises(queue.Empty):
        runtime.outgoing.get_nowait()


def test_ui_pump_health_ages_out():
    runtime = _runtime()
    runtime.last_ui_tick_monotonic = time.monotonic() - UI_TICK_STALE_SECONDS - 1.0
    assert runtime._ui_pump_healthy() is False


def test_tick_outer_boundary_never_raises():
    runtime = _runtime()

    def explode():
        raise ValueError("unexpected tick boundary failure")

    runtime._tick_impl = explode
    runtime.tick()

    assert runtime.last_ui_tick_error is not None
    assert runtime.last_ui_tick_error["exception_type"] == "ValueError"


def test_stale_tick_history_does_not_pre_reject_next_command(monkeypatch):
    runtime = _runtime()
    called = []
    monkeypatch.setattr(client, "dispatch", lambda hou, command, session: (called.append(command["command_id"]) or _success(command)))
    runtime.last_ui_tick_monotonic = time.monotonic() - UI_TICK_STALE_SECONDS - 20.0

    runtime._enqueue_ui_command(_command("cmd-after-long-cook"))

    with pytest.raises(queue.Empty):
        runtime.outgoing.get_nowait()

    runtime.tick()

    result = runtime.outgoing.get_nowait()
    assert result["command_id"] == "cmd-after-long-cook"
    assert result["status"] == "success"
    assert called == ["cmd-after-long-cook"]



def test_long_host_dispatch_arms_bounded_recovery_grace():
    runtime = _runtime()

    duration = runtime._note_host_dispatch(started=100.0, ended=106.0)

    assert duration == 6.0
    assert runtime.ui_recovery_grace_until == 166.0


def test_short_host_dispatch_does_not_arm_recovery_grace():
    runtime = _runtime()

    duration = runtime._note_host_dispatch(started=100.0, ended=102.0)

    assert duration == 2.0
    assert runtime.ui_recovery_grace_until == 0.0


def test_pending_command_waits_through_post_long_host_grace_then_expires():
    runtime = _runtime()
    runtime._note_host_dispatch(started=100.0, ended=106.0)
    runtime._enqueue_ui_command(_command("cmd-recovery-grace"), now=110.0)

    assert runtime._expire_stalled_ui_commands(now=120.0) == 0
    with pytest.raises(queue.Empty):
        runtime.outgoing.get_nowait()

    assert runtime._expire_stalled_ui_commands(now=167.0) == 1
    result = runtime.outgoing.get_nowait()
    assert result["command_id"] == "cmd-recovery-grace"
    assert result["failure"]["code"] == "ADAPTER_UI_PUMP_STALLED"


def test_connection_config_refresh_follows_supervisor_port_change(monkeypatch):
    configs = [
        {"url": "http://127.0.0.1:8765", "token": "same-token"},
        {"url": "http://127.0.0.1:8766", "token": "same-token"},
    ]
    monkeypatch.setattr(client, "load_connection_config", lambda: dict(configs.pop(0)))
    runtime = HoudiniBridgeRuntime(FakeHou())
    runtime.registered = True
    runtime.last_heartbeat = 123.0

    assert runtime._connection_snapshot()["url"].endswith(":8765")
    assert runtime._refresh_connection_config() is True
    assert runtime._connection_snapshot()["url"].endswith(":8766")
    assert runtime.registered is False
    assert runtime.last_heartbeat == 0.0


def test_explicit_connection_config_is_not_reloaded(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(
        client,
        "load_connection_config",
        lambda: {"url": "http://127.0.0.1:9999", "token": "other"},
    )
    assert runtime._refresh_connection_config() is False
    assert runtime._connection_snapshot() == {"url": "http://127.0.0.1:9", "token": "test"}
