from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_phase3_import_fix_template_uses_frameinfo_on_animated_pose():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    item = templates["kinefx.import_fix_triplet"]
    assert item["state"] == "validated"
    assert item["supported_host_versions"] == ["21.0.440"]

    roles = {node["role"]: node for node in item["nodes"]}
    assert roles["import"]["type"] == "kinefx::Sop/fbxcharacterimport"
    assert roles["frame_info"]["type"] == "Sop/null"

    wiring = item["wiring"]
    assert {
        "from_role": "import",
        "from_output": 2,
        "to_role": "frame_info",
        "to_input": 0,
        "semantic": "Animated Pose",
    } in wiring
    assert {
        "from_role": "frame_info",
        "from_output": 0,
        "to_role": "input_fix",
        "to_input": 2,
        "semantic": "Animated Pose via frame-info",
    } in wiring
    assert not any(
        row["from_role"] == "import"
        and row["from_output"] == 2
        and row["to_role"] == "input_fix"
        for row in wiring
    )


def test_phase3_fbx_character_import_aliases_are_validated():
    aliases = {item["id"]: item for item in knowledge_registry.alias_sets()}
    item = aliases["fbx_character_import.file_parameters"]
    assert item["state"] == "validated"
    assert item["supported_host_versions"] == ["21.0.440"]
    by_semantic = {row["semantic"]: row for row in item["aliases"]}
    assert by_semantic["import_file"]["internal_name"] == "fbxfile"
    assert by_semantic["animation_fbx_file"]["internal_name"] == "animfbxfile"
    assert len(item["validation_evidence"]) >= 3


def test_phase3_fbx_character_output_alias_tokens_are_validated():
    aliases = {item["id"]: item for item in knowledge_registry.alias_sets()}
    item = aliases["fbx_output.ui_semantics"]
    assert item["state"] == "validated"
    assert item["node_type"] == "kinefx::Sop/rop_fbxcharacteroutput"
    assert item["supported_host_versions"] == ["21.0.440"]

    by_semantic = {row["semantic"]: row for row in item["aliases"]}
    assert by_semantic["frame_range_mode"]["internal_name"] == "cliprangemode"
    assert by_semantic["frame_start"]["internal_name"] == "f1"
    assert by_semantic["frame_end"]["internal_name"] == "f2"
    assert by_semantic["frame_increment"]["internal_name"] == "f3"
    assert by_semantic["input_fbx_file"]["internal_name"] == "inputfilepath"
    assert by_semantic["output_fbx_file"]["internal_name"] == "outputfilepath"
    assert by_semantic["clip_name"]["internal_name"] == "clipname"


def test_phase3_output_triplet_template_is_validated_project_family():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    item = templates["kinefx.output_triplet_to_fbx_character"]
    assert item["state"] == "validated"
    assert item["scope"] == "project_family"
    assert item["supported_host_versions"] == ["21.0.440"]
    final_hops = [
        row for row in item["wiring"]
        if row["from_role"] == "output_transform_adapter"
        and row["to_role"] == "fbx_character_output"
    ]
    assert {(row["from_output"], row["to_input"]) for row in final_hops} == {
        (0, 0), (1, 1), (2, 2)
    }


def test_phase3_set_animation_recipe_is_bounded_and_safe():
    recipe = knowledge_registry.get_recipe("fbx.character_import.set_animation_and_cook")
    assert recipe["promotion_state"] == "promoted"
    assert recipe["supported_host_versions"] == ["21.0.440"]
    assert recipe["supported_node_types"] == ["kinefx::Sop/fbxcharacterimport"]
    assert recipe["required"] == ["path", "animation_file"]

    assert [step["op"] for step in recipe["steps"]] == [
        "parm.read",
        "parm.write",
        "cook.execute",
        "host.errors",
    ]
    assert recipe["steps"][0]["arguments"]["parameter"] == "animfbxfile"
    assert recipe["steps"][1]["arguments"]["parameter"] == "animfbxfile"
    assert recipe["steps"][1]["arguments"]["expected_hash"] == {"$result": "read.hash"}
    assert all(step["op"] != "hip.save" for step in recipe["steps"])

    check = knowledge_registry.validate_recipe(
        "fbx.character_import.set_animation_and_cook",
        {
            "path": "/obj/geo1/IMPORT_ANIM",
            "animation_file": "C:/test/example.fbx",
        },
        hou=None,
    )
    assert check["ok"] is True
    assert not check["errors"]


def test_phase3_promotion_registry_records_validated_scope_and_new_recipe():
    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}

    assert entries["template.kinefx.import_fix_triplet"]["state"] == "validated"
    assert entries["template.kinefx.output_triplet_to_fbx_character"]["state"] == "validated"
    assert entries["alias.fbx_character_import.file_parameters"]["state"] == "validated"
    assert entries["alias.fbx_character_output.core_parameters"]["state"] == "validated"

    recipe = entries["recipe.fbx.character_import.set_animation_and_cook"]
    assert recipe["state"] == "promoted"
    assert recipe["validation"]["regression_pass"] is True
    assert recipe["validation"]["live_or_baseline_pass"] is True


def test_phase3_status_growth_is_expected():
    status = knowledge_registry.status()
    assert status["recipe_count"] >= 6
    assert status["promotion_entry_count"] >= 9
    assert status["candidate_count"] >= 1
    assert status["validated_count"] >= 5
    assert status["promoted_count"] >= 3
    assert status["template_count"] >= 4
    assert status["alias_set_count"] >= 2
    assert status["knowledge_degraded"] is False
