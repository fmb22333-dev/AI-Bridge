from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.app import build_runtime


def test_build_runtime_bootstrap_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(tmp_path))
    app, service, connection, runtime_state = build_runtime(
        data_dir=tmp_path / "data",
        port=18765,
    )

    assert app is not None
    assert service is not None
    assert connection["url"] == "http://127.0.0.1:18765"
    assert runtime_state["remote"]["status"] == "unconfigured"

    desc = service.adapter_registry.get("bridge_admin")
    restart = next(
        cap for cap in desc.capabilities
        if cap.name == "bridge.plugin.restart_apply"
    )
    assert restart.manages_checkpoint is True


def test_health_exposes_remote_transport_activity(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(tmp_path))
    app, service, connection, runtime_state = build_runtime(
        data_dir=tmp_path / "data-health",
        port=18766,
    )
    runtime_state["remote"] = {
        "configured": True,
        "status": "connected",
        "activity": {
            "last_poll_at": "2026-09-15T00:00:00+00:00",
            "last_poll_finished_at": "2026-09-15T00:00:01+00:00",
            "active_execution_count": 0,
        },
    }
    health_endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/health")

    payload = health_endpoint()

    assert payload["remote"]["status"] == "connected"
    assert payload["remote"]["activity"]["last_poll_finished_at"] == "2026-09-15T00:00:01+00:00"
