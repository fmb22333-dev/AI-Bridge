from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_phase5_constructor_aliases_distinguish_display_and_create_tokens():
    aliases = {item["id"]: item for item in knowledge_registry.alias_sets()}
    item = aliases["houdini.node_type.constructor_tokens"]
    assert item["state"] == "validated"
    assert item["supported_host_versions"] == ["21.0.440"]

    by_semantic = {row["semantic"]: row for row in item["aliases"]}
    assert by_semantic["fbx_character_import"]["inspect_display"] == "kinefx::Sop/fbxcharacterimport"
    assert by_semantic["fbx_character_import"]["constructor_token"] == "kinefx::fbxcharacterimport"
    assert by_semantic["null"]["inspect_display"] == "Sop/null"
    assert by_semantic["null"]["constructor_token"] == "null"


def test_phase5_constructor_token_host_rule_is_promoted():
    rules = {item["id"]: item for item in knowledge_registry.host_rules()}
    item = rules["node_type.constructor_token_not_display_name"]
    assert item["promotion_state"] == "promoted"
    assert item["supported_host_versions"] == ["21.0.440"]
    examples = {
        row["display"]: row["constructor"]
        for row in item["validated_examples"]
    }
    assert examples["kinefx::Sop/fbxcharacterimport"] == "kinefx::fbxcharacterimport"
    assert examples["Sop/null"] == "null"


def test_phase5_import_frameinfo_template_is_validated_and_self_contained():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    item = templates["kinefx.import_frameinfo_build"]
    assert item["state"] == "validated"
    assert item["scope"] == "kinefx_node_type_21_0_440"
    assert item["syntax_sugar_recipe"] == "kinefx.import_with_frameinfo"

    roles = {row["role"]: row for row in item["roles"]}
    assert roles["import"]["constructor_type"] == "kinefx::fbxcharacterimport"
    assert roles["frame_info"]["constructor_type"] == "null"
    assert item["wiring"] == [
        {
            "from_role": "import",
            "from_output": 2,
            "to_role": "frame_info",
            "to_input": 0,
            "semantic": "Animated Pose",
        }
    ]


def test_phase5_import_with_frameinfo_recipe_is_promoted_bounded_sugar():
    recipe = knowledge_registry.get_recipe("kinefx.import_with_frameinfo")
    assert recipe["promotion_state"] == "promoted"
    assert recipe["scope"] == "kinefx_node_type_21_0_440"
    assert recipe["required"] == [
        "parent",
        "import_name",
        "frame_info_name",
        "import_file",
        "animation_file",
    ]
    assert recipe["optional"] == []
    assert recipe["compression"] == {
        "caller_arguments": 5,
        "expanded_nodes": 2,
        "expanded_parameter_writes": 2,
        "expanded_connections": 1,
        "existing_node_rewires": 0,
    }

    assert [step["op"] for step in recipe["steps"]] == [
        "network.validate",
        "network.apply",
    ]

    for step in recipe["steps"]:
        args = step["arguments"]
        nodes = args["nodes"]
        assert len(nodes) == 2
        by_id = {node["id"]: node for node in nodes}
        assert by_id["import"]["type"] == "kinefx::fbxcharacterimport"
        assert by_id["frame_info"]["type"] == "null"
        assert set(by_id["import"]["parms"]) == {"fbxfile", "animfbxfile"}
        assert args["connections"] == [
            {"source": "import", "output": 2, "target": "frame_info", "input": 0}
        ]
        assert all(
            not str(conn["target"]).startswith("/")
            for conn in args["connections"]
        )

    check = knowledge_registry.validate_recipe(
        "kinefx.import_with_frameinfo",
        {
            "parent": "/obj/geo1",
            "import_name": "__TEST_IMPORT",
            "frame_info_name": "__TEST_FRAMEINFO",
            "import_file": "C:/test/rest.fbx",
            "animation_file": "C:/test/anim.fbx",
        },
        hou=None,
    )
    assert check["ok"] is True
    assert not check["errors"]


def test_phase5_promotion_registry_tracks_recipe_and_constructor_alias():
    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}

    recipe = entries["recipe.kinefx.import_with_frameinfo"]
    assert recipe["state"] == "promoted"
    assert recipe["validation"]["regression_pass"] is True
    assert recipe["validation"]["live_or_baseline_pass"] is True

    alias = entries["alias.houdini.node_type.constructor_tokens"]
    assert alias["state"] == "validated"
    assert alias["validation"]["regression_pass"] is True
    assert alias["validation"]["live_or_baseline_pass"] is True


def test_phase5_knowledge_growth_counts():
    status = knowledge_registry.status()
    assert status["recipe_count"] >= 7
    assert status["host_rule_count"] >= 30
    assert status["promotion_entry_count"] >= 12
    assert status["validated_count"] >= 6
    assert status["promoted_count"] >= 4
    assert status["template_count"] >= 6
    assert status["alias_set_count"] >= 3
    assert status["knowledge_degraded"] is False
