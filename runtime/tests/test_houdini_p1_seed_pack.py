from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_p1_seed_pack_host_rules_present():
    rules = {item["id"]: item for item in knowledge_registry.host_rules()}
    expected = {
        "kinefx.world_joint_attribute_contract",
        "vex.generic_return_explicit_temporaries",
        "vex.function_parameters_readonly",
        "vex.buffer_then_commit",
        "vex.language_is_not_cpp",
        "numeric.degenerate_guard",
        "hom.require_node_and_parameter",
        "hom.validate_connection_indices",
        "hom.geometry_api_versioned",
        "validation.warnings_not_automatic_pass",
        "validation.technical_visual_separate",
        "validation.harness_fail_on_empty_or_exception",
        "fbx.output_range_mode_explicit",
        "fbx.import_animation_path_readback",
        "manual_update.cook_auto_window",
        "hip.no_cpio_directory_entries",
        "hip.archive_member_order",
        "hip.python_sop_roundtrip_compile",
        "performance.freeze_static_reference",
        "diagnostics.detail_attributes",
    }
    assert expected <= set(rules)
    assert len(rules) >= 29
    for rule_id in expected:
        rule = rules[rule_id]
        assert rule["promotion_state"] == "promoted"
        assert "21.0.440" in rule["supported_host_versions"]


def test_p1_seed_pack_error_signatures_match_historical_failures():
    cases = [
        (
            "Read-only expression given for read/write parameter",
            "VEX_FUNCTION_PARAMETER_READ_ONLY",
        ),
        (
            'Macro "PI" redefined',
            "VEX_BUILTIN_MACRO_REDEFINED",
        ),
        (
            "Syntax error, unexpected const",
            "VEX_TOP_LEVEL_CONST_UNSUPPORTED",
        ),
        (
            "Call to undefined function 'substr'",
            "VEX_FUNCTION_NOT_AVAILABLE",
        ),
        (
            "hou.InvalidInput: Invalid input.",
            "HOUDINI_INVALID_INPUT_CONNECTION",
        ),
        (
            "ValueError: attrib_name cannot be None",
            "HOUDINI_ATTRIBUTE_NAME_REQUIRED",
        ),
        (
            "ValueError: illegal newline value",
            "PYTHON_ILLEGAL_NEWLINE_ARGUMENT",
        ),
        (
            'Error: Unknown operator on load "SOURCE_ANIM_CACHE.def".',
            "HOUDINI_HIP_UNKNOWN_OPERATOR",
        ),
        (
            "Missing file extension: obj",
            "HOUDINI_HIP_CPIO_DIRECTORY_ENTRY",
        ),
        (
            "AttributeError: 'Geometry' object has no attribute 'iterVertices'",
            "HOUDINI_HOM_API_UNAVAILABLE",
        ),
        (
            "View state disabled due to degenerate topology in input.",
            "HOUDINI_DEGENERATE_TOPOLOGY",
        ),
    ]
    for message, expected_code in cases:
        rule = knowledge_registry.match_error_text(message)
        assert rule is not None, message
        assert rule["code"] == expected_code, (message, rule)


def test_p1_cook_checked_recipe_is_guard_valid():
    recipe = knowledge_registry.get_recipe("cook.checked")
    assert [step["op"] for step in recipe["steps"]] == [
        "cook.execute",
        "host.errors",
    ]
    check = knowledge_registry.validate_recipe(
        "cook.checked",
        {"path": "/obj/geo1/OUT"},
        hou=None,
    )
    assert check["ok"] is True
    assert not check["errors"]


def test_p1_parm_safe_write_and_cook_recipe_is_guard_valid():
    recipe = knowledge_registry.get_recipe("parm.safe_write_and_cook")
    assert [step["op"] for step in recipe["steps"]] == [
        "parm.read",
        "parm.write",
        "cook.execute",
        "host.errors",
    ]
    write_step = recipe["steps"][1]
    assert write_step["arguments"]["expected_hash"] == {"$result": "read.hash"}
    check = knowledge_registry.validate_recipe(
        "parm.safe_write_and_cook",
        {
            "path": "/obj/geo1/START",
            "parameter": "example",
            "value": 1,
            "cook_path": "/obj/geo1/OUT",
        },
        hou=None,
    )
    assert check["ok"] is True
    assert not check["errors"]


def test_p1_seed_pack_counts_expand_initial_knowledge():
    status = knowledge_registry.status()
    assert status["recipe_count"] >= 5
    assert status["error_rule_count"] >= 15
    assert status["host_rule_count"] >= 29
    assert status["knowledge_degraded"] is False
