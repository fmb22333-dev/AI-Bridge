from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HOUDINI_PY = ROOT / "houdini_adapter" / "python"
for path in (SRC, HOUDINI_PY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionInfo
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge_houdini.dispatcher import _same_project as adapter_same_project


def _service(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", root)
    return BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
        execution_policy=ExecutionPolicyStore(tmp_path / "execution_policy.json"),
    )


def test_windows_project_identity_accepts_separator_and_drive_case_aliases():
    left = r"E:\AA\backup\UVAutoChart_test.hip"
    right = "e:/AA/backup/UVAutoChart_test.hip"
    assert BridgeService._same_project(left, right)
    assert adapter_same_project(left, right)
    assert not BridgeService._same_project(left, "E:/AA/backup/other.hip")
    assert not adapter_same_project(left, "E:/AA/backup/other.hip")


def test_active_sessions_excludes_stale_registry_entries(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=60)).isoformat()
    fresh = now.isoformat()
    service.sessions.register(SessionInfo(
        session_id="HOU-STALE",
        adapter="houdini",
        adapter_version="0.5.14",
        host_version="21.0.440",
        pid=111,
        project_file="E:/AA/old.hip",
        registered_at=stale,
        last_seen_at=stale,
    ), preserve_timestamps=True)
    service.sessions.register(SessionInfo(
        session_id="HOU-LIVE",
        adapter="houdini",
        adapter_version="0.5.15",
        host_version="21.0.440",
        pid=222,
        project_file="E:/AA/live.hip",
        registered_at=fresh,
        last_seen_at=fresh,
    ), preserve_timestamps=True)

    active = service.active_sessions(adapter="houdini")
    assert [item.session_id for item in active] == ["HOU-LIVE"]


def test_plugin_surfaces_use_only_active_sessions():
    app = (SRC / "ai_bridge" / "app.py").read_text(encoding="utf-8")
    routes = (SRC / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")
    assert "service.plugins.status(live_sessions=service.active_sessions())" in app
    assert "service.plugins.install_all(live_sessions=service.active_sessions())" in app
    assert "live_sessions=service.active_sessions()" in routes
    assert "unreal_projects=_unreal_project_files()" in routes
    assert "live_sessions=service.sessions.list()" not in routes
