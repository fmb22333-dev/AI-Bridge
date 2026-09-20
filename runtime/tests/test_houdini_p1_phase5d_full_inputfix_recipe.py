from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_phase5d_full_inputfix_recipe_is_promoted_only_in_bounded_scope():
    recipe = knowledge_registry.get_recipe("retarget.fbx_import_to_input_fix")
    assert recipe["promotion_state"] == "promoted"
    assert recipe["scope"] == "retarget_inputfix_contract_21_0_440"
    assert recipe["supported_host_versions"] == ["21.0.440"]
    assert recipe["required"] == [
        "parent",
        "import_name",
        "frame_info_name",
        "import_file",
        "animation_file",
        "input_fix_path",
    ]
    assert recipe["compression"] == {
        "caller_arguments": 6,
        "expanded_state_reads": 3,
        "expanded_nodes": 2,
        "expanded_parameter_writes": 2,
        "expanded_connections": 4,
        "existing_node_rewires": 3,
        "transaction_cleanup": True,
    }


def test_phase5d_recipe_reads_all_external_hashes_before_transaction():
    recipe = knowledge_registry.get_recipe("retarget.fbx_import_to_input_fix")
    assert [step["op"] for step in recipe["steps"]] == [
        "node.input_state",
        "node.input_state",
        "node.input_state",
        "network.apply_transactional",
    ]
    assert [step["save_as"] for step in recipe["steps"][:3]] == [
        "input0",
        "input1",
        "input2",
    ]
    assert [step["arguments"]["input_index"] for step in recipe["steps"][:3]] == [0, 1, 2]
    for step in recipe["steps"][:3]:
        assert step["arguments"]["target"] == {"$arg": "input_fix_path"}


def test_phase5d_transaction_shape_matches_validated_three_stream_contract():
    recipe = knowledge_registry.get_recipe("retarget.fbx_import_to_input_fix")
    tx = recipe["steps"][3]
    assert tx["op"] == "network.apply_transactional"
    args = tx["arguments"]

    nodes = {node["id"]: node for node in args["nodes"]}
    assert nodes["import"]["type"] == "kinefx::fbxcharacterimport"
    assert nodes["frame_info"]["type"] == "null"
    assert set(nodes["import"]["parms"]) == {"fbxfile", "animfbxfile"}

    assert args["connections"] == [
        {"source": "import", "output": 2, "target": "frame_info", "input": 0},
        {
            "source": "import",
            "output": 0,
            "target": {"$arg": "input_fix_path"},
            "input": 0,
            "expected_hash": {"$result": "input0.hash"},
        },
        {
            "source": "import",
            "output": 1,
            "target": {"$arg": "input_fix_path"},
            "input": 1,
            "expected_hash": {"$result": "input1.hash"},
        },
        {
            "source": "frame_info",
            "output": 0,
            "target": {"$arg": "input_fix_path"},
            "input": 2,
            "expected_hash": {"$result": "input2.hash"},
        },
    ]


def test_phase5d_recipe_resolves_with_deferred_hashes_without_host_mutation():
    check = knowledge_registry.validate_recipe(
        "retarget.fbx_import_to_input_fix",
        {
            "parent": "/obj/geo1",
            "import_name": "__TEST_IMPORT",
            "frame_info_name": "__TEST_FRAMEINFO",
            "import_file": "C:/test/rest.fbx",
            "animation_file": "C:/test/anim.fbx",
            "input_fix_path": "/obj/geo1/inputFix",
        },
        hou=None,
    )
    assert check["ok"] is True
    assert not check["errors"]
    tx = check["resolved_steps"][3]["arguments"]
    assert tx["connections"][1]["expected_hash"] == {"$deferred_result": "input0.hash"}
    assert tx["connections"][2]["expected_hash"] == {"$deferred_result": "input1.hash"}
    assert tx["connections"][3]["expected_hash"] == {"$deferred_result": "input2.hash"}


def test_phase5d_template_records_assumptions_heuristics_and_validation_status():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    item = templates["kinefx.import_fix_triplet"]
    assert item["syntax_sugar_recipe"] == "retarget.fbx_import_to_input_fix"
    assert item["executable_scope"] == "retarget_inputfix_contract_21_0_440"
    assert item["transactional"] is True
    assert item["assumptions"]
    assert item["heuristics"]
    assert item["validation_status"]["adapter"] == "0.5.6"


def test_phase5d_promotion_entry_preserves_nonuniversal_contract():
    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}
    item = entries["recipe.retarget.fbx_import_to_input_fix"]
    assert item["state"] == "promoted"
    assert item["scope"] == "retarget_inputfix_contract_21_0_440"
    assert item["bounded_contract"]["universal"] is False
    assert item["bounded_contract"]["input_fix_inputs"] == {
        "0": "Rest Geometry from FBX Character Import output 0",
        "1": "Capture Pose from FBX Character Import output 1",
        "2": "Animated Pose from FRAMEINFO output 0",
    }
    assert item["validation"]["regression_pass"] is True
    assert item["validation"]["live_or_baseline_pass"] is True


def test_phase5d_knowledge_counts_include_full_recipe():
    status = knowledge_registry.status()
    assert status["recipe_count"] >= 8
    assert status["promotion_entry_count"] >= 13
    assert status["promoted_count"] >= 5
    assert status["knowledge_degraded"] is False
