from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.sessions import SessionInfo, SessionRegistry


def test_touch_revives_stale_session_without_changing_identity():
    registry = SessionRegistry(stale_after_seconds=1.0)
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    info = SessionInfo(
        session_id="HOU-LONG-COOK",
        adapter="houdini",
        adapter_version="0.5.9",
        host_version="21.0.440",
        pid=1234,
        project_file="E:/AA/UVAutoChart_V0_13_1_UVAudit.hip",
        registered_at=old,
        last_seen_at=old,
    )
    registry.register(info, preserve_timestamps=True)

    assert registry.status(info.session_id).state == "disconnected"

    touched = registry.touch(info.session_id)

    assert touched.session_id == info.session_id
    assert touched.pid == info.pid
    assert touched.project_file == info.project_file
    assert touched.registered_at == old
    assert registry.status(info.session_id).state == "connected"
