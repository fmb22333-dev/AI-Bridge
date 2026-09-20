from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import capability_guidance, knowledge_registry
from ai_bridge_houdini.client import CAPABILITIES


def test_every_recipe_has_machine_readable_lifecycle_authority():
    recipes = {item["id"]: item for item in knowledge_registry.recipes()}
    entries = {
        str(item.get("target") or "").removeprefix("recipe:"): item
        for item in knowledge_registry.promotion_entries()
        if item.get("kind") == "recipe"
    }

    assert set(recipes) == set(entries)
    for recipe_id, recipe in recipes.items():
        assert recipe["promotion_state"] == entries[recipe_id]["state"]
        assert recipe["execution_authorized"] is (
            entries[recipe_id]["state"] == "promoted"
        )


def test_promoted_guidance_never_points_to_missing_capability():
    implemented = {item["name"] for item in CAPABILITIES}
    for item in capability_guidance.catalog(promoted_only=True)["entries"]:
        assert item["capability"] in implemented


def test_deprecated_recipes_have_valid_promoted_replacement():
    recipes = {item["id"]: item for item in knowledge_registry.recipes()}
    for item in knowledge_registry.promotion_entries():
        if item.get("kind") != "recipe" or item.get("state") != "deprecated":
            continue
        replacement = item.get("superseded_by")
        assert replacement, item["id"]
        assert replacement in recipes
        assert recipes[replacement]["promotion_state"] == "promoted"


def test_recipe_primitives_are_registered_adapter_capabilities():
    implemented = {item["name"] for item in CAPABILITIES}
    for summary in knowledge_registry.recipes():
        recipe = knowledge_registry.get_recipe(summary["id"])
        for step in recipe.get("steps") or []:
            assert step["op"] in implemented, (summary["id"], step["op"])


def test_recipe_get_uses_registry_authority():
    old = knowledge_registry.get_recipe("network.build_and_cook")
    assert old["promotion_state"] == "deprecated"
    assert old["execution_authorized"] is False
    assert old["superseded_by"] == "network.ensure_and_cook"

    current = knowledge_registry.get_recipe("network.ensure_and_cook")
    assert current["promotion_state"] == "promoted"
    assert current["execution_authorized"] is True
