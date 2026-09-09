from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


PROJECT_SCOPES = {
    "project_family",
    "animation_project_family",
}


class KnowledgePublishGateError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise KnowledgePublishGateError(f"{path.name} must contain a JSON object")
    return data


def _scope_is_project_specific(value: Any) -> bool:
    scope = str(value or "").strip().lower()
    if not scope:
        return False
    if scope in PROJECT_SCOPES:
        return True
    return scope.endswith("_project_family") or scope.startswith("project_family")


def _implemented_capability_names(source_root: Path) -> set[str]:
    client_path = source_root.parent / "client.py"
    tree = ast.parse(client_path.read_text(encoding="utf-8"), filename=str(client_path))
    payload = None
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = []
            value = None
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value = node.value
            elif isinstance(node.target, ast.Name):
                names = [node.target.id]
                value = node.value
            if "CAPABILITIES" not in names or value is None:
                continue
            payload = ast.literal_eval(value)
            break
    if not isinstance(payload, list):
        raise KnowledgePublishGateError("client.py CAPABILITIES must be a literal list")
    names = {
        str(item.get("name") or "").strip()
        for item in payload
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    if not names:
        raise KnowledgePublishGateError("client.py CAPABILITIES is empty")
    return names


def validate_source_knowledge(
    source_root: Path,
    *,
    require_capability_authority: bool = True,
) -> dict:
    source_root = Path(source_root)
    promotion = _read_json(source_root / "promotion_registry.json")
    guidance = _read_json(source_root / "capability_guidance.json")
    client_path = source_root.parent / "client.py"
    capability_authority_available = client_path.is_file()
    implemented = (
        _implemented_capability_names(source_root)
        if capability_authority_available
        else set()
    )

    recipes: dict[str, dict] = {}
    recipe_files: dict[str, str] = {}
    issues: list[dict] = []
    if require_capability_authority and not capability_authority_available:
        issues.append({
            "code": "CAPABILITY_AUTHORITY_MISSING",
            "path": str(client_path),
        })

    for path in sorted((source_root / "recipes").glob("*.json")):
        recipe = _read_json(path)
        recipe_id = str(recipe.get("id") or "").strip()
        if not recipe_id:
            issues.append({"code": "RECIPE_ID_MISSING", "file": path.name})
            continue
        if recipe_id in recipes:
            issues.append({
                "code": "RECIPE_ID_DUPLICATE",
                "recipe": recipe_id,
                "files": [recipe_files[recipe_id], path.name],
            })
            continue
        recipes[recipe_id] = recipe
        recipe_files[recipe_id] = path.name

    lifecycle: dict[str, dict] = {}
    for item in promotion.get("entries") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "recipe":
            continue
        target = str(item.get("target") or "").strip()
        if not target.startswith("recipe:"):
            issues.append({
                "code": "RECIPE_LIFECYCLE_TARGET_INVALID",
                "id": item.get("id"),
                "target": target,
            })
            continue
        recipe_id = target.split(":", 1)[1].strip()
        if recipe_id in lifecycle:
            issues.append({
                "code": "RECIPE_LIFECYCLE_DUPLICATE",
                "recipe": recipe_id,
            })
            continue
        lifecycle[recipe_id] = item

    for recipe_id in sorted(set(recipes) - set(lifecycle)):
        issues.append({"code": "RECIPE_LIFECYCLE_MISSING", "recipe": recipe_id})
    for recipe_id in sorted(set(lifecycle) - set(recipes)):
        issues.append({"code": "LIFECYCLE_RECIPE_MISSING", "recipe": recipe_id})

    for recipe_id, recipe in recipes.items():
        entry = lifecycle.get(recipe_id)
        if entry is not None:
            registry_state = str(entry.get("state") or "").strip().lower()
            metadata_state = str(
                recipe.get("promotion_state")
                or recipe.get("lifecycle_state")
                or ""
            ).strip().lower()
            if metadata_state and metadata_state != registry_state:
                issues.append({
                    "code": "RECIPE_METADATA_STATE_DRIFT",
                    "recipe": recipe_id,
                    "recipe_state": metadata_state,
                    "registry_state": registry_state,
                })

        for index, step in enumerate(recipe.get("steps") or []):
            if not isinstance(step, dict):
                issues.append({
                    "code": "RECIPE_STEP_INVALID",
                    "recipe": recipe_id,
                    "step_index": index,
                })
                continue
            operation = str(step.get("op") or "").strip()
            if not operation:
                issues.append({
                    "code": "RECIPE_PRIMITIVE_MISSING",
                    "recipe": recipe_id,
                    "step_index": index,
                })
            elif capability_authority_available and operation not in implemented:
                issues.append({
                    "code": "RECIPE_PRIMITIVE_UNIMPLEMENTED",
                    "recipe": recipe_id,
                    "step_index": index,
                    "operation": operation,
                })

    for item in guidance.get("entries") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("state") or "").strip().lower() != "promoted":
            continue
        capability = str(item.get("capability") or "").strip()
        if capability_authority_available and capability and capability not in implemented:
            issues.append({
                "code": "GUIDANCE_CAPABILITY_MISSING",
                "id": item.get("id"),
                "capability": capability,
            })

    for recipe_id, entry in lifecycle.items():
        if str(entry.get("state") or "").strip().lower() != "deprecated":
            continue
        replacement = str(entry.get("superseded_by") or "").strip()
        if not replacement:
            issues.append({
                "code": "DEPRECATED_REPLACEMENT_MISSING",
                "recipe": recipe_id,
            })
            continue
        replacement_recipe = recipes.get(replacement)
        replacement_entry = lifecycle.get(replacement)
        if replacement_recipe is None or replacement_entry is None:
            issues.append({
                "code": "DEPRECATED_REPLACEMENT_UNKNOWN",
                "recipe": recipe_id,
                "superseded_by": replacement,
            })
            continue
        if str(replacement_entry.get("state") or "").strip().lower() != "promoted":
            issues.append({
                "code": "DEPRECATED_REPLACEMENT_NOT_PROMOTED",
                "recipe": recipe_id,
                "superseded_by": replacement,
                "replacement_state": replacement_entry.get("state"),
            })

    distributable_promoted_recipes = []
    project_scoped_promoted_recipes = []
    for recipe_id, entry in lifecycle.items():
        if str(entry.get("state") or "").strip().lower() != "promoted":
            continue
        recipe = recipes.get(recipe_id) or {}
        if _scope_is_project_specific(entry.get("scope")) or _scope_is_project_specific(recipe.get("scope")):
            project_scoped_promoted_recipes.append(recipe_id)
        else:
            distributable_promoted_recipes.append(recipe_id)

    return {
        "ok": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "capability_authority_available": capability_authority_available,
        "implemented_capability_count": len(implemented),
        "recipe_count": len(recipes),
        "recipe_lifecycle_count": len(lifecycle),
        "promoted_guidance_count": sum(
            1
            for item in guidance.get("entries") or []
            if isinstance(item, dict)
            and str(item.get("state") or "").strip().lower() == "promoted"
        ),
        "distributable_promoted_recipes": sorted(distributable_promoted_recipes),
        "project_scoped_promoted_recipes": sorted(project_scoped_promoted_recipes),
    }


def require_source_knowledge_publishable(
    source_root: Path,
    *,
    require_capability_authority: bool = True,
) -> dict:
    report = validate_source_knowledge(
        source_root,
        require_capability_authority=require_capability_authority,
    )
    if report["ok"]:
        return report
    summary = "; ".join(
        str(item.get("code") or "UNKNOWN")
        + (
            f"[{item.get('recipe')}]"
            if item.get("recipe")
            else ""
        )
        for item in report["issues"]
    )
    raise KnowledgePublishGateError(
        "Knowledge publish gate failed: " + summary
    )
