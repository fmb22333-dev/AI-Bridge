from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


def _load_supervisor():
    root = Path(__file__).resolve().parents[3]
    path = root / "_System" / "supervisor.py"
    spec = importlib.util.spec_from_file_location("ai_bridge_supervisor_watchdog_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_supervisor_watchdog_detects_stale_connected_transport():
    supervisor = _load_supervisor()
    payload = {
        "remote": {
            "configured": True,
            "status": "connected",
            "activity": {"last_poll_finished_at": "2026-09-15T00:00:00+00:00"},
        }
    }
    reason = supervisor._transport_watchdog_reason(
        payload,
        now=datetime(2026, 9, 15, 0, 1, 0, tzinfo=timezone.utc),
        stale_seconds=45.0,
    )
    assert reason is not None
    assert "stale" in reason


def test_supervisor_watchdog_ignores_fresh_and_intentional_degraded_states():
    supervisor = _load_supervisor()
    now = datetime(2026, 9, 15, 0, 0, 30, tzinfo=timezone.utc)
    fresh = {
        "remote": {
            "configured": True,
            "status": "connected",
            "activity": {"last_poll_finished_at": "2026-09-15T00:00:00+00:00"},
        }
    }
    assert supervisor._transport_watchdog_reason(fresh, now=now, stale_seconds=45.0) is None
    for status in ("auth_degraded", "rate_limited", "disabled", "unconfigured", "credential_missing"):
        payload = {"remote": {"configured": True, "status": status, "activity": {"last_poll_finished_at": "2026-09-14T00:00:00+00:00"}}}
        assert supervisor._transport_watchdog_reason(payload, now=now, stale_seconds=45.0) is None


def test_supervisor_restart_policy_requires_three_health_failures_or_stale_transport():
    supervisor = _load_supervisor()
    assert supervisor._watchdog_restart_reason(False, None, 2) is None
    assert "health failed" in supervisor._watchdog_restart_reason(False, None, 3)
    stale = {
        "remote": {
            "configured": True,
            "status": "connected",
            "activity": {"last_poll_finished_at": "2026-09-15T00:00:00+00:00"},
        }
    }
    reason = supervisor._watchdog_restart_reason(
        True,
        stale,
        0,
        now=datetime(2026, 9, 15, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert reason is not None and "stale" in reason
