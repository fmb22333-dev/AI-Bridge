from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor, descriptor


def _seed_runtime(runtime: Path):
    runtime.mkdir(parents=True)
    (runtime / "pyproject.toml").write_text('[project]\nname="test"\nversion = "9.9.9"\n', encoding="utf-8")
    (runtime / "launch_bridge.py").write_text("pass\n", encoding="utf-8")
    (runtime / "install_houdini_adapter.py").write_text("pass\n", encoding="utf-8")
    (runtime / "src" / "ai_bridge").mkdir(parents=True)
    (runtime / "src" / "ai_bridge" / "__init__.py").write_text("", encoding="utf-8")
    source_knowledge = ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
    target_knowledge = runtime / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
    target_knowledge.parent.mkdir(parents=True)
    shutil.copytree(source_knowledge, target_knowledge)


def _seed_supervisor(root: Path):
    system = root / "_System"
    system.mkdir(parents=True)
    (system / "supervisor.py").write_text('SUPERVISOR_VERSION = "0.1.2"\n', encoding="utf-8")
    (system / "bootstrap.bat").write_text("@echo off\n", encoding="utf-8")
    (system / "VERSION.txt").write_text("stale\n", encoding="utf-8")
    for name in ("AI_Bridge.bat", "START_AI_BRIDGE.bat", "OPEN_AI_BRIDGE.bat", "STOP_AI_BRIDGE.bat", "README_FIRST.txt"):
        (root / name).write_text("@echo off\n" if name.endswith(".bat") else "readme\n", encoding="utf-8")
    (system / "supervisor_before_0.1.2.py").write_text("old\n", encoding="utf-8")


def test_bridge_admin_exposes_clean_installer_builder():
    cap = next(item for item in descriptor().capabilities if item.name == "bridge.bootstrap.build_installer")
    assert cap.write is True
    assert str(cap.risk).endswith("L1")


def test_distribution_bundle_is_clean_while_development_bundle_keeps_full_knowledge(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    _seed_runtime(executor.staging)

    dev_zip = tmp_path / "dev.zip"
    clean_zip = tmp_path / "clean.zip"
    executor._build_bundle(dev_zip)
    manifest = executor._build_distribution_bundle(clean_zip)

    with zipfile.ZipFile(dev_zip) as zf:
        dev_names = set(zf.namelist())
    with zipfile.ZipFile(clean_zip) as zf:
        clean_names = set(zf.namelist())
        registry = json.loads(zf.read("runtime/houdini_adapter/python/ai_bridge_houdini/knowledge/promotion_registry.json"))
    assert "runtime/houdini_adapter/python/ai_bridge_houdini/knowledge/recipes/retarget.fbx_import_to_input_fix.json" in dev_names
    assert "runtime/houdini_adapter/python/ai_bridge_houdini/knowledge/recipes/retarget.fbx_import_to_input_fix.json" not in clean_names
    assert all(item["state"] == "promoted" for item in registry["entries"])
    assert all(item.get("scope") not in {"project_family", "animation_project_family"} for item in registry["entries"])
    assert manifest["content_digest"]


def test_clean_installer_contains_fresh_runtime_and_no_generated_state(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    _seed_supervisor(root)
    _seed_runtime(root / "Runtime" / "Current")
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")

    archive = tmp_path / "installer.zip"
    result = executor._build_clean_installer_archive(archive)
    assert result["supervisor_version"] == "0.1.2"
    assert result["runtime_version"] == "9.9.9"
    assert result["validation"]["ok"] is True

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        version_text = zf.read("AI_Bridge/_System/VERSION.txt").decode("utf-8")
        registry_text = zf.read("AI_Bridge/Runtime/Current/houdini_adapter/python/ai_bridge_houdini/knowledge/promotion_registry.json").decode("utf-8")
    assert "AI_Bridge/INSTALL_AI_BRIDGE.bat" in names
    assert "AI_Bridge/Runtime/Current/pyproject.toml" in names
    assert not any("supervisor_before_" in name for name in names)
    assert not any("/Runtime/Versions/" in name or "/Runtime/Staging/" in name for name in names)
    assert "Supervisor 0.1.2" in version_text
    assert "SOURCE_DEFAULT_GEOMETRY" not in registry_text
