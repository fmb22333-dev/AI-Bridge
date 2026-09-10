from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .knowledge_gate import require_source_knowledge_publishable


PROJECT_SCOPES = {
    "project_family",
    "animation_project_family",
}

METADATA_KEYS_TO_STRIP = {
    "evidence",
    "validation_evidence",
    "state_history",
    "promotion_requirements",
    "historical_names",
    "historical_path",
    "evidence_chain",
    "validation_status",
    "provenance",
}

PROJECT_RECIPE_PREFIXES = (
    "retarget.",
    "autouv.",
    "auto_uv.",
)


class DistributionKnowledgeError(RuntimeError):
    pass


def default_source_root() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "houdini_adapter"
        / "python"
        / "ai_bridge_houdini"
        / "knowledge"
    )


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DistributionKnowledgeError(f"{path.name} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # Distribution artifacts are byte-stable across platforms. Path.write_text()
    # may translate LF to CRLF on Windows, which previously changed release digests.
    path.write_bytes(serialized.encode("utf-8"))


def _scope_is_project_specific(value: Any) -> bool:
    scope = str(value or "").strip().lower()
    if not scope:
        return False
    if scope in PROJECT_SCOPES:
        return True
    return scope.endswith("_project_family") or scope.startswith("project_family")


def _strip_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in METADATA_KEYS_TO_STRIP:
                continue
            out[key] = _strip_metadata(item)
        return out
    if isinstance(value, list):
        return [_strip_metadata(item) for item in value]
    return value


def _is_distribution_state(value: Any) -> bool:
    return str(value or "").strip().lower() in {"validated", "promoted"}


def _filter_promotion_registry(source: dict) -> dict:
    entries = []
    for item in source.get("entries") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("state") or "").lower() != "promoted":
            continue
        if _scope_is_project_specific(item.get("scope")):
            continue
        entries.append(_strip_metadata(item))
    return {
        "schema_version": str(source.get("schema_version") or "1.0"),
        "lifecycle": list(source.get("lifecycle") or []),
        "allowed_transitions": dict(source.get("allowed_transitions") or {}),
        "promotion_gate": dict(source.get("promotion_gate") or {}),
        "entries": entries,
    }


def _filter_templates(source: dict) -> dict:
    templates = []
    for item in source.get("templates") or []:
        if not isinstance(item, dict):
            continue
        if not _is_distribution_state(item.get("state")):
            continue
        if _scope_is_project_specific(item.get("scope")):
            continue
        executable_scope = str(item.get("executable_scope") or "").lower()
        if executable_scope.startswith(("retarget_", "autouv_", "auto_uv_")):
            continue
        templates.append(_strip_metadata(item))
    return {
        "schema_version": str(source.get("schema_version") or "1.0"),
        "templates": templates,
    }


def _filter_aliases(source: dict) -> dict:
    alias_sets = []
    for item in source.get("alias_sets") or []:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "validated").lower()
        if state not in {"validated", "promoted"}:
            continue
        if _scope_is_project_specific(item.get("scope")):
            continue
        alias_sets.append(_strip_metadata(item))
    return {
        "schema_version": str(source.get("schema_version") or "1.0"),
        "alias_sets": alias_sets,
    }


def _filter_guidance(source: dict) -> dict:
    entries = []
    for item in source.get("entries") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("state") or "").lower() != "promoted":
            continue
        if _scope_is_project_specific(item.get("scope")):
            continue
        entries.append(_strip_metadata(item))
    return {
        "schema_version": str(source.get("schema_version") or "1.0"),
        "policy": _strip_metadata(source.get("policy") or {}),
        "entries": entries,
    }


def _filter_rule_document(source: dict) -> dict:
    return {
        "rules": [
            _strip_metadata(rule)
            for rule in source.get("rules") or []
            if isinstance(rule, dict)
        ]
    }


def _promoted_recipe_ids(promotion: dict) -> set[str]:
    out = set()
    for item in promotion.get("entries") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "recipe":
            continue
        if str(item.get("state") or "").strip().lower() != "promoted":
            continue
        target = str(item.get("target") or "").strip()
        if target.startswith("recipe:"):
            out.add(target.split(":", 1)[1].strip().lower())
    return out


def _recipe_is_distributable(recipe: dict, promoted_recipe_ids: set[str]) -> bool:
    recipe_id = str(recipe.get("id") or "").strip().lower()
    if not recipe_id or recipe_id not in promoted_recipe_ids:
        return False
    if recipe_id.startswith(PROJECT_RECIPE_PREFIXES):
        return False
    if _scope_is_project_specific(recipe.get("scope")):
        return False
    scope = str(recipe.get("scope") or "").lower()
    if scope.startswith(("retarget_", "autouv_", "auto_uv_")):
        return False
    return True


def _canonical_text_bytes(path: Path) -> bytes:
    # Clean Knowledge is UTF-8 JSON. Normalize legacy/worktree newline variants
    # before hashing so LF/CRLF checkout policy cannot change release identity.
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _content_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "distribution_manifest.json"):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_text_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_distribution(root: Path) -> dict:
    root = Path(root)
    promotion = _read_json(root / "promotion_registry.json")
    templates = _read_json(root / "template_catalog.json")
    guidance = _read_json(root / "capability_guidance.json")
    promoted_recipe_ids = _promoted_recipe_ids(promotion)

    violations = []
    for item in promotion.get("entries") or []:
        if item.get("state") != "promoted":
            violations.append(f"non-promoted registry entry: {item.get('id')}")
        if _scope_is_project_specific(item.get("scope")):
            violations.append(f"project-family registry entry: {item.get('id')}")
    for item in templates.get("templates") or []:
        if _scope_is_project_specific(item.get("scope")):
            violations.append(f"project-family template: {item.get('id')}")
    for item in guidance.get("entries") or []:
        if item.get("state") != "promoted":
            violations.append(f"non-promoted guidance: {item.get('id')}")

    for path in sorted((root / "recipes").glob("*.json")):
        recipe = _read_json(path)
        if not _recipe_is_distributable(recipe, promoted_recipe_ids):
            violations.append(f"unpromoted/project recipe: {recipe.get('id') or path.name}")

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.json"))
        if path.name != "distribution_manifest.json"
    )
    forbidden_literals = (
        "SOURCE_DEFAULT_GEOMETRY",
        "AUTO_UV_CURRENT_STATE",
        '"scope": "project_family"',
        '"scope": "animation_project_family"',
        '"historical_path"',
        '"historical_names"',
    )
    for literal in forbidden_literals:
        if literal in serialized:
            violations.append(f"forbidden distribution literal: {literal}")

    return {
        "ok": not violations,
        "violations": violations,
        "content_digest": _content_digest(root),
    }


def build_distribution_knowledge(
    destination: Path,
    *,
    source_root: Path | None = None,
) -> dict:
    source_root = Path(source_root or default_source_root())
    destination = Path(destination)

    if not source_root.is_dir():
        raise FileNotFoundError(source_root)

    source_gate = require_source_knowledge_publishable(
        source_root,
        require_capability_authority=False,
    )

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    recipes_out = destination / "recipes"
    recipes_out.mkdir(parents=True, exist_ok=True)

    _write_json(
        destination / "error_catalog.json",
        _filter_rule_document(_read_json(source_root / "error_catalog.json")),
    )
    _write_json(
        destination / "host_rules.json",
        _filter_rule_document(_read_json(source_root / "host_rules.json")),
    )
    promotion_source = _read_json(source_root / "promotion_registry.json")
    promoted_recipe_ids = _promoted_recipe_ids(promotion_source)
    _write_json(
        destination / "promotion_registry.json",
        _filter_promotion_registry(promotion_source),
    )
    _write_json(
        destination / "template_catalog.json",
        _filter_templates(_read_json(source_root / "template_catalog.json")),
    )
    _write_json(
        destination / "alias_catalog.json",
        _filter_aliases(_read_json(source_root / "alias_catalog.json")),
    )
    _write_json(
        destination / "capability_guidance.json",
        _filter_guidance(_read_json(source_root / "capability_guidance.json")),
    )

    included_recipes = []
    excluded_recipes = []
    for source_path in sorted((source_root / "recipes").glob("*.json")):
        recipe = _read_json(source_path)
        if not _recipe_is_distributable(recipe, promoted_recipe_ids):
            excluded_recipes.append(str(recipe.get("id") or source_path.stem))
            continue
        cleaned = _strip_metadata(recipe)
        _write_json(recipes_out / source_path.name, cleaned)
        included_recipes.append(str(cleaned.get("id") or source_path.stem))

    validation = validate_distribution(destination)
    if not validation["ok"]:
        raise DistributionKnowledgeError(
            "Distribution knowledge validation failed: "
            + "; ".join(validation["violations"])
        )

    manifest = {
        "schema_version": "1.0",
        "mode": "clean_distribution",
        "source_root": str(source_root),
        "included_recipes": included_recipes,
        "excluded_recipes": excluded_recipes,
        "promotion_entry_count": len(_read_json(destination / "promotion_registry.json").get("entries") or []),
        "template_count": len(_read_json(destination / "template_catalog.json").get("templates") or []),
        "alias_set_count": len(_read_json(destination / "alias_catalog.json").get("alias_sets") or []),
        "guidance_count": len(_read_json(destination / "capability_guidance.json").get("entries") or []),
        "content_digest": validation["content_digest"],
        "source_gate": source_gate,
    }
    _write_json(destination / "distribution_manifest.json", manifest)
    return manifest
