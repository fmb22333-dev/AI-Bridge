from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.plugin_manager import HostPluginManager


def test_manifest_exposes_unreal_as_installable(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_HOUDINI_USER_DIR", str(tmp_path / "houdini21.0"))
    manager = HostPluginManager(ROOT)
    status = manager.status()
    by_id = {item["id"]: item for item in status["hosts"]}
    assert by_id["houdini"]["state"] == "ready"
    assert by_id["houdini"]["bundled_version"] == "0.5.27"
    assert by_id["unreal"]["state"] == "ready"
    assert by_id["unreal"]["supports_install"] is True
    assert by_id["unreal"]["supports_restart_apply"] is False
    assert by_id["unreal"]["bundled_version"] == "0.5.3"
    assert by_id["blender"]["state"] == "scaffold"


def test_unreal_status_discovers_project_target(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_HOUDINI_USER_DIR", str(tmp_path / "houdini21.0"))
    project = tmp_path / "Game.uproject"
    project.write_text("{}", encoding="utf-8")
    manager = HostPluginManager(ROOT)
    status = manager.status(unreal_projects=[str(project)])
    unreal = next(item for item in status["hosts"] if item["id"] == "unreal")
    assert unreal["project_file"] == str(project.resolve())
    assert unreal["installed"] is False
    assert unreal["targets"][0]["install_path"].endswith("Plugins\\AIBridgeUE") or unreal["targets"][0]["install_path"].endswith("Plugins/AIBridgeUE")


def test_install_all_keeps_unreal_project_scoped(tmp_path, monkeypatch):
    user_dir = tmp_path / "houdini21.0"
    monkeypatch.setenv("AI_BRIDGE_HOUDINI_USER_DIR", str(user_dir))
    manager = HostPluginManager(ROOT)
    result = manager.install_all(unreal_projects=[str(tmp_path / "Game.uproject")])
    by_id = {item["host_id"]: item for item in result["results"]}
    assert by_id["houdini"].get("skipped") is not True
    assert by_id["unreal"]["reason"] == "USE_PER_PROJECT_INSTALL"
    assert by_id["blender"]["reason"] == "INSTALLER_NOT_READY"


def test_live_houdini_version_mismatch_requires_restart(tmp_path, monkeypatch):
    user_dir = tmp_path / "houdini21.0"
    monkeypatch.setenv("AI_BRIDGE_HOUDINI_USER_DIR", str(user_dir))
    manager = HostPluginManager(ROOT)
    manager.install("houdini", host_running=False)

    class Session:
        adapter = "houdini"
        session_id = "HOU-OLD"
        pid = 123
        project_file = "E:/AA/test.hip"
        adapter_version = "0.5.13"
        host_version = "21.0.440"

    status = manager.status(live_sessions=[Session()])
    houdini = next(item for item in status["hosts"] if item["id"] == "houdini")
    assert houdini["disk_up_to_date"] is True
    assert houdini["live_up_to_date"] is False
    assert houdini["restart_required"] is True


def test_builtin_manifest_fallback_has_unreal_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_HOUDINI_USER_DIR", str(tmp_path / "houdini21.0"))
    manager = HostPluginManager(ROOT)
    manager.manifest_path = tmp_path / "missing-host-plugins.json"
    status = manager.status()
    by_id = {item["id"]: item for item in status["hosts"]}
    assert by_id["unreal"]["state"] == "ready"
