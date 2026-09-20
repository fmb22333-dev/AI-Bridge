from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.deployment.knowledge_gate import validate_source_knowledge


SOURCE = ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "knowledge"
    shutil.copytree(SOURCE, target)
    shutil.copy2(
        ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "client.py",
        target.parent / "client.py",
    )
    return target


def test_current_source_passes_publish_gate():
    report = validate_source_knowledge(SOURCE)
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["implemented_capability_count"] >= 50
    assert report["recipe_count"] == report["recipe_lifecycle_count"]


def test_gate_rejects_recipe_metadata_registry_drift(tmp_path):
    target = _copy(tmp_path)
    path = target / "recipes" / "network.build.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["lifecycle_state"] = "promoted"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_source_knowledge(target)
    assert report["ok"] is False
    assert any(
        item["code"] == "RECIPE_METADATA_STATE_DRIFT"
        and item["recipe"] == "network.build"
        for item in report["issues"]
    )


def test_gate_rejects_unimplemented_recipe_primitive(tmp_path):
    target = _copy(tmp_path)
    path = target / "recipes" / "network.build.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["steps"][0]["op"] = "network.not_real"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_source_knowledge(target)
    assert report["ok"] is False
    assert any(
        item["code"] == "RECIPE_PRIMITIVE_UNIMPLEMENTED"
        and item["operation"] == "network.not_real"
        for item in report["issues"]
    )


def test_gate_rejects_invalid_deprecated_replacement(tmp_path):
    target = _copy(tmp_path)
    path = target / "promotion_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["entries"]:
        if item.get("target") == "recipe:network.build_and_cook":
            item["superseded_by"] = "network.build"
            break
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_source_knowledge(target)
    assert report["ok"] is False
    assert any(
        item["code"] == "DEPRECATED_REPLACEMENT_NOT_PROMOTED"
        and item["recipe"] == "network.build_and_cook"
        for item in report["issues"]
    )
