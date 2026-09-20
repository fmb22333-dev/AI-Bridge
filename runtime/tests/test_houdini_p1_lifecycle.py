from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


VALID_STATES = {"observed", "candidate", "validated", "promoted", "deprecated"}


def test_p1_lifecycle_registry_is_machine_readable():
    entries = knowledge_registry.promotion_entries()
    assert entries
    ids = {item["id"] for item in entries}
    assert "template.kinefx.import_fix_triplet" in ids
    assert "alias.fbx_character_import.file_parameters" in ids
    assert "recipe.cook.checked" in ids
    assert all(item["state"] in VALID_STATES for item in entries)


def test_p1_promoted_entries_have_required_evidence():
    for item in knowledge_registry.promotion_entries():
        if item["state"] != "promoted":
            continue
        assert item.get("evidence")
        validation = item.get("validation") or {}
        assert validation.get("regression_pass") is True
        assert validation.get("live_or_baseline_pass") is True


def test_p1_project_family_entries_do_not_silently_promote():
    for item in knowledge_registry.promotion_entries():
        if item.get("scope") in {"project_family", "animation_project_family"}:
            assert item["state"] != "promoted"


def test_p1_template_catalog_preserves_role_scope():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    triplet = templates["kinefx.import_fix_triplet"]
    assert triplet["state"] in {"candidate", "validated"}
    assert triplet["state"] != "promoted"
    assert triplet["scope"] == "project_family"
    import_role = next(item for item in triplet["nodes"] if item["role"] == "import")
    assert import_role["type"] == "kinefx::Sop/fbxcharacterimport"


def test_p1_alias_catalog_preserves_exact_observed_tokens():
    aliases = {item["id"]: item for item in knowledge_registry.alias_sets()}
    fbx = aliases["fbx_character_import.file_parameters"]
    by_semantic = {item["semantic"]: item for item in fbx["aliases"]}
    assert by_semantic["import_file"]["internal_name"] == "fbxfile"
    assert by_semantic["animation_fbx_file"]["internal_name"] == "animfbxfile"
    assert fbx["state"] in {"candidate", "validated"}
    assert fbx["state"] != "promoted"


def test_p1_search_surfaces_nonpromoted_catalogs_without_making_them_authority():
    result = knowledge_registry.search("animfbxfile", limit=20)
    assert result["count"] >= 1
    hit = next(item for item in result["results"] if item.get("kind") == "alias_set")
    assert hit["id"] == "fbx_character_import.file_parameters"
    assert hit["state"] in {"candidate", "validated"}
    assert hit["state"] != "promoted"


def test_p1_status_exposes_lifecycle_counts():
    status = knowledge_registry.status()
    assert status["promotion_entry_count"] >= 7
    assert status["candidate_count"] >= 1
    assert status["validated_count"] >= 1
    assert status["promoted_count"] >= 2
    assert status["template_count"] >= 4
    assert status["alias_set_count"] >= 2
    assert status["knowledge_degraded"] is False
