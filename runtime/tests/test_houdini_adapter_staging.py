from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"src"
if str(SRC) not in sys.path:
    sys.path.insert(0,str(SRC))

from ai_bridge.adapters.bridge_admin import descriptor


def _load_installer():
    path=ROOT/"install_houdini_adapter.py"
    spec=importlib.util.spec_from_file_location("test_houdini_installer",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_source(root: Path):
    source=root/"source"
    pkg=source/"python"/"ai_bridge_houdini"
    scripts=source/"scripts"
    package=source/"package"
    pkg.mkdir(parents=True)
    scripts.mkdir(parents=True)
    package.mkdir(parents=True)
    (pkg/"__init__.py").write_text('__version__ = "9.9.9"\n',encoding="utf-8")
    (pkg/"new_file.py").write_text("VALUE=1\n",encoding="utf-8")
    (scripts/"456.py").write_text("print('ok')\n",encoding="utf-8")
    (package/"ai_bridge_houdini.json").write_text(json.dumps({"path":"x"}),encoding="utf-8")
    return source


def test_running_safe_stage_preserves_existing_files(tmp_path):
    installer=_load_installer()
    source=_fake_source(tmp_path)
    user=tmp_path/"houdini21.0"
    existing=user/"ai_bridge_houdini"/"python"/"ai_bridge_houdini"
    existing.mkdir(parents=True)
    stale=existing/"stale_runtime_module.py"
    stale.write_text("STILL_LOADED=True\n",encoding="utf-8")

    result=installer.install_adapter(user,source=source,running_safe=True)

    assert result["changed"] is True
    assert result["running_safe"] is True
    assert stale.exists()
    assert (existing/"new_file.py").exists()
    marker=json.loads((user/"ai_bridge_houdini"/".ai_bridge_source.json").read_text(encoding="utf-8"))
    assert marker["running_safe_stage"] is True


def test_cold_install_remains_clean_replacement(tmp_path):
    installer=_load_installer()
    source=_fake_source(tmp_path)
    user=tmp_path/"houdini21.0"
    existing=user/"ai_bridge_houdini"/"python"/"ai_bridge_houdini"
    existing.mkdir(parents=True)
    stale=existing/"stale_runtime_module.py"
    stale.write_text("OLD=True\n",encoding="utf-8")

    result=installer.install_adapter(user,source=source,running_safe=False)

    assert result["changed"] is True
    assert result["running_safe"] is False
    assert not stale.exists()
    assert (existing/"new_file.py").exists()


def test_stage_houdini_capability_is_explicit_and_non_rollback():
    desc=descriptor()
    cap=next(item for item in desc.capabilities if item.name=="bridge.adapter.stage_houdini")
    assert cap.write is True
    assert cap.rollback is False
    assert str(cap.risk).endswith("L1")


def test_bridge_admin_stage_path_never_requests_live_reload():
    text=(SRC/"ai_bridge"/"adapters"/"bridge_admin.py").read_text(encoding="utf-8")
    assert "running_safe=True" in text
    assert '"hot_reload_performed": False' in text
    assert "importlib.reload" not in text


def test_force_clean_replaces_stale_files_even_when_marker_hash_matches(tmp_path):
    installer=_load_installer()
    source=_fake_source(tmp_path)
    user=tmp_path/"houdini21.0"

    first=installer.install_adapter(user,source=source,running_safe=True)
    assert first["changed"] is True

    existing=user/"ai_bridge_houdini"/"python"/"ai_bridge_houdini"
    stale=existing/"stale_runtime_module.py"
    stale.write_text("OLD=True\n",encoding="utf-8")

    second=installer.install_adapter(
        user,
        source=source,
        running_safe=False,
        force_clean=True,
    )

    assert second["changed"] is True
    assert second["force_clean"] is True
    assert not stale.exists()
    assert (existing/"new_file.py").exists()
