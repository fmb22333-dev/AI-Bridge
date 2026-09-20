from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import unreal_adapter.install_plugin as installer


def _make_plugin(root: Path, version: str = "0.5.3") -> Path:
    root.mkdir(parents=True)
    (root / "AIBridgeUE.uplugin").write_text(
        json.dumps({"FileVersion": 3, "VersionName": version}), encoding="utf-8"
    )
    source = root / "Source" / "AIBridgeUEEditor"
    source.mkdir(parents=True)
    (source / "Test.cpp").write_text("int x = 1;\n", encoding="utf-8")
    (root / "Binaries").mkdir()
    (root / "Binaries" / "stale.dll").write_bytes(b"stale")
    return root


def test_source_hash_ignores_build_artifacts(tmp_path):
    source = _make_plugin(tmp_path / "source")
    before = installer._source_hash(source)
    (source / "Intermediate").mkdir()
    (source / "Intermediate" / "temp.obj").write_bytes(b"x")
    assert installer._source_hash(source) == before


def test_install_replaces_plugin_and_drops_build_artifacts(tmp_path, monkeypatch):
    project = tmp_path / "Game.uproject"
    project.write_text("{}", encoding="utf-8")
    source = _make_plugin(tmp_path / "bundled")
    old = _make_plugin(tmp_path / "Plugins" / "AIBridgeUE", version="0.4.0")
    assert (old / "Binaries").exists()
    monkeypatch.setattr(installer, "_assert_unreal_closed", lambda _project: None)

    result = installer.install_plugin(project, source=source)

    target = tmp_path / "Plugins" / "AIBridgeUE"
    assert result["changed"] is True
    assert result["installed_version"] == "0.5.3"
    assert not (target / "Binaries").exists()
    assert not (target / "Intermediate").exists()
    marker = json.loads((target / ".ai_bridge_source.json").read_text(encoding="utf-8"))
    assert marker["source_hash"] == installer._source_hash(source)


def test_install_refuses_missing_uproject(tmp_path, monkeypatch):
    source = _make_plugin(tmp_path / "bundled")
    monkeypatch.setattr(installer, "_assert_unreal_closed", lambda _project: None)
    try:
        installer.install_plugin(tmp_path / "Missing.uproject", source=source)
    except FileNotFoundError as exc:
        assert "UNREAL_PROJECT_NOT_FOUND" in str(exc)
    else:
        raise AssertionError("missing project must fail")
