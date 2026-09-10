from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.deployment.knowledge_pack import (
    DISTRIBUTABLE_SCOPE_PREFIXES,
    _scope_is_distribution_generic,
    build_distribution_knowledge,
    validate_distribution,
)


def test_distribution_knowledge_filters_project_family_and_candidate_content(tmp_path):
    target = tmp_path / "knowledge"
    manifest = build_distribution_knowledge(
        target,
        source_root=ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge",
    )

    promotion = json.loads((target / "promotion_registry.json").read_text(encoding="utf-8"))
    templates = json.loads((target / "template_catalog.json").read_text(encoding="utf-8"))
    guidance = json.loads((target / "capability_guidance.json").read_text(encoding="utf-8"))

    assert promotion["entries"]
    assert all(item["state"] == "promoted" for item in promotion["entries"])
    assert all(
        _scope_is_distribution_generic(item.get("scope"))
        for item in promotion["entries"]
        if item.get("kind") == "recipe"
    )
    assert all(item.get("scope") not in {"project_family", "animation_project_family"} for item in promotion["entries"])
    assert all(item.get("scope") not in {"project_family", "animation_project_family"} for item in templates["templates"])
    assert all(item["state"] == "promoted" for item in guidance["entries"])

    recipes = {path.stem for path in (target / "recipes").glob("*.json")}
    assert "cook.checked" in recipes
    assert "parm.safe_write_and_cook" in recipes
    assert "network.ensure_and_cook" in recipes
    assert "code.safe_patch_and_cook" in recipes

    serialized = "\n".join(path.read_text(encoding="utf-8") for path in target.rglob("*.json"))
    assert "SOURCE_DEFAULT_GEOMETRY" not in serialized
    assert '"historical_path"' not in serialized
    assert '"historical_names"' not in serialized
    assert '"provenance"' not in serialized
    assert '"validation_status"' not in serialized
    assert manifest["content_digest"] == validate_distribution(target)["content_digest"]
    assert manifest["source_root"] == "ai_bridge_houdini/knowledge"


def test_distribution_knowledge_is_deterministic(tmp_path):
    source = ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
    first = build_distribution_knowledge(tmp_path / "one", source_root=source)
    second = build_distribution_knowledge(tmp_path / "two", source_root=source)
    assert first["content_digest"] == second["content_digest"]
    assert first["included_recipes"] == second["included_recipes"]
    assert first["excluded_recipes"] == second["excluded_recipes"]


def test_distribution_digest_is_newline_independent(tmp_path):
    source = ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
    target = tmp_path / "knowledge-newlines"
    manifest = build_distribution_knowledge(target, source_root=source)

    for path in sorted(target.rglob("*.json")):
        if path.name == "distribution_manifest.json":
            continue
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    assert validate_distribution(target)["content_digest"] == manifest["content_digest"]


def test_distribution_recipe_execution_authority_matches_promoted_registry(tmp_path):
    target = tmp_path / "knowledge-authority"
    manifest = build_distribution_knowledge(
        target,
        source_root=ROOT / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge",
    )
    promotion = json.loads((target / "promotion_registry.json").read_text(encoding="utf-8"))
    promoted = {
        str(item.get("target")).split(":", 1)[1]
        for item in promotion.get("entries") or []
        if item.get("kind") == "recipe"
        and item.get("state") == "promoted"
        and str(item.get("target") or "").startswith("recipe:")
    }
    included = set(manifest["included_recipes"])
    assert included <= promoted
    assert "code.safe_patch_and_cook" in included


def test_distribution_scope_allowlist_rejects_project_specific_contracts():
    assert DISTRIBUTABLE_SCOPE_PREFIXES == ("universal_", "houdini_", "kinefx_")
    assert _scope_is_distribution_generic("houdini_declarative_network_21_0_440")
    assert _scope_is_distribution_generic("kinefx_point_skeleton")
    assert not _scope_is_distribution_generic("custom_project_contract_21_0_440")
    assert not _scope_is_distribution_generic("project_family")
