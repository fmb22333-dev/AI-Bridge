from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import capability_guidance


def _node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"Node not found: {path}")
    return node


def _call(obj, name: str, default=None):
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn()
    except Exception:
        return default


ARGUMENT_SCHEMAS = {
    "capability.search": {
        "required": {"query": "str"},
        "optional": {"limit": "int 1..100"},
        "write": False,
        "searches": [
            "live implemented adapter capabilities",
            "argument schemas",
            "capability guidance",
            "recipe lifecycle/promotion authority",
            "deprecated/superseded recipe metadata",
        ],
        "matching": "deterministic exact-name/substring plus partial token-overlap ranking",
    },
    "diagnostic.transaction": {
        "required": {"parent": "str"},
        "optional": {
            "mode": "run|cleanup; default run",
            "diagnostic_id": "stable id; run defaults to command_id, cleanup requires explicit prior id",
            "nodes": "run: list[temp node spec] 1..32",
            "connections": "run: list[connection] <=64; target must be a temp ref; source may be temp ref or absolute existing node path",
            "cook_ref": "run: temporary node ref to Cook",
            "collect": "run: list[geometry.query spec] <=16 using temporary node refs",
        },
        "write": True,
        "risk": "L2",
        "transaction": "isolated namespaced temp nodes -> marker -> Cook -> bounded geometry collection -> finally cleanup",
        "recovery": "unknown/timeout leftovers can be removed with mode=cleanup + prior diagnostic_id; cleanup matches ai_bridge_diagnostic_id userData only",
        "safety": "existing nodes may only appear as connection sources; external targets are forbidden",
    },
    "geometry.query": {
        "required": {"path": "str"},
        "optional": {
            "owner": "detail|point|prim|vertex; default point",
            "attributes": "list[str]; omitted selects available attributes up to max_attributes",
            "key_attribute": "str semantic key/filter attribute",
            "key_values": "list[any] or scalar; omitted means truthy filter",
            "mode": "summary|rows|stats; default summary",
            "frames": "list[number] 1..64; uses geometryAtFrame and never frame.set",
            "max_rows": "int 0..1000; default 200",
            "max_attributes": "int 1..64; default 32",
        },
        "write": False,
        "summary": "bounded geometry/attribute query with host-safe multi-frame sampling and no temporary nodes",
    },
    "parm.multiparm.ensure": {
        "required": {"path": "str", "count_parameter": "str", "minimum_count": "int 0..10000"},
        "optional": {
            "values": "object mapping generated child parm names to desired values; max 256",
            "expected_count_hash": "optional optimistic guard for the observed count parameter",
        },
        "write": True,
        "semantics": "grow-or-keep only; never shrinks a multiparm",
        "transaction": "snapshot -> optional count growth -> resolve generated children -> write -> readback; any failure restores prior values/count",
    },
    "inspect.parm_template": {
        "required": {"path": "str", "parameter": "str"},
        "write": False,
        "summary": "read Houdini ParmTemplate metadata including callback/menu/default/condition fields without pressing buttons or mutating the HIP",
    },
    "inspect.session_module": {
        "optional": {
            "symbol": "top-level function/class name; when omitted return the module",
            "max_chars": "int 1..1000000; default 200000",
        },
        "write": False,
        "summary": "read the HIP Python Source Module via hou.sessionModuleSource without executing it; optionally extract one top-level symbol with AST",
    },
    "inspect.batch_nodes": {
        "required": {"paths": "list[str] (1..1000)"},
        "optional": {
            "mode": "summary|normal|deep",
            "detail_level": "alias of mode",
            "fields": "list[str] top-level node fields to retain",
            "max_details": "int 0..1000; bounds successful node data payloads",
        },
        "partial_failure": "per-item success/data/error; one missing node does not abort the batch",
        "large_output": "requested/succeeded/failed remain complete when successful detail rows are omitted",
    },
    "parm.batch_read": {
        "canonical": {"items": "list[{path:str, parameter:str}]"},
        "aliases": {
            "path+parameters": {"path": "str", "parameters": "list[str]"},
        },
        "empty_input": "ARGUMENT_REQUIRED",
    },
    "parm.batch_write": {
        "canonical": {
            "items": "list[{path:str, parameter:str, value:any, expected_hash:str}]"
        },
        "aliases": {
            "path+writes": {"path": "str", "writes": "list[write]"},
        },
        "write": {"parameter": "str", "value": "any", "expected_hash": "str"},
        "preflight": "all expected hashes are checked before the first mutation",
    },
    "node.batch_connect": {
        "canonical": {
            "items": "list[{target:str, input_index:int, source:str, output_index:int, expected_hash:str}]"
        },
        "aliases": {
            "target+connections": {"target": "str", "connections": "list[connection]"}
        },
        "preflight": "all expected input hashes are checked before the first mutation",
        "rollback": "prior writes are restored if a later setInput/readback fails",
    },
    "network.apply_transactional": {
        "required": {"parent": "str", "nodes": "list[node]", "connections": "list[connection]"},
        "external_connection": {
            "target": "existing node path",
            "input_index": "int",
            "source": "new-node ref or existing node path",
            "output_index": "int",
            "expected_hash": "str"
        },
        "transaction": "external hash preflight -> create/internal wiring -> atomic external batch connect; cleanup newly created nodes on external failure"
    },
    "network.ensure_plan": {
        "required": {"parent": "str", "nodes": "list[node]", "connections": "list[connection]"},
        "optional": {"cook_path": "node ref/path", "max_details": "int 0..1000"},
        "write": False,
        "summary": "complete change counts plus compact CREATE / UPDATE_PARMS / UPDATE_STATE / CONNECT / NOOP string",
        "details": "bounded node/connection diff; summary is never truncated",
        "semantics": "read-only advisory snapshot of what network.ensure_transactional would reconcile at the time of the call",
        "concurrency_note": "plan is not a lock; use expected-hash APIs when later writes must remain conditional on previously observed state"
    },
    "network.ensure_transactional": {
        "required": {"parent": "str", "nodes": "list[node]", "connections": "list[connection]"},
        "optional": {"cook_path": "node ref/path", "force": "bool", "layout": "bool", "expected_plan_hash": "str from network.ensure_plan.plan_hash"},
        "semantics": "authoritatively reconcile only explicitly declared parameters, node-state fields, and target inputs; unspecified node data is untouched",
        "idempotency": "matching existing nodes are reused; equal declared values and connections are skipped",
        "transaction": "snapshot existing declared fields/inputs -> create/update/connect -> optional cook -> readback; any exception, readback failure, or cook error restores existing declared state and removes nodes created by this call",
        "concurrency_note": "optional expected_plan_hash guards Plan -> Ensure handoff against declared-state drift; use parm.write/node.batch_connect expected-hash APIs for finer-grained writes conditional on earlier external reads"
    },
    "knowledge.search": {
        "query": "str",
        "limit": "int",
        "matching": ["exact_substring", "all_tokens"],
        "tokenization": "case-insensitive; punctuation and underscores are token separators",
    },
}


def adapter_capabilities(hou, session_info: dict) -> dict:
    return {
        "adapter": "houdini",
        "adapter_version": session_info.get("adapter_version"),
        "host": "Houdini",
        "host_version": hou.applicationVersionString(),
        "session_id": session_info.get("session_id"),
        "project_file": hou.hipFile.path(),
        "capabilities": capability_guidance.enrich_capabilities(list(session_info.get("capabilities") or [])),
        "argument_schemas": ARGUMENT_SCHEMAS,
        "capability_guidance": capability_guidance.catalog(promoted_only=False),
    }


def _capability_search_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\W_]+", str(value or "").lower()) if token]


def _capability_search_score(query: str, payload: dict, primary_name: str) -> dict | None:
    query = str(query or "").strip().lower()
    if not query:
        return None
    primary = str(primary_name or "").strip().lower()
    hay = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    if query == primary:
        return {"mode": "exact_name", "score": 10000}
    if query in primary:
        return {"mode": "name_substring", "score": 7000 + len(query)}
    if query in hay:
        return {"mode": "exact_substring", "score": 5000 + len(query)}

    query_tokens = _capability_search_tokens(query)
    if not query_tokens:
        return None
    hay_tokens = set(_capability_search_tokens(hay))
    overlap = sum(1 for token in query_tokens if token in hay_tokens)
    if overlap <= 0:
        return None
    coverage = overlap / max(1, len(query_tokens))
    return {
        "mode": "token_overlap",
        "score": 1000 + overlap * 100 + int(coverage * 100),
        "matched_tokens": overlap,
        "query_tokens": len(query_tokens),
    }


def capability_integrity(session_info: dict) -> dict:
    from . import knowledge_registry

    implemented = {
        str(item.get("name") or "")
        for item in session_info.get("capabilities") or []
        if isinstance(item, dict) and str(item.get("name") or "")
    }
    recipes = {
        item["id"]: item
        for item in knowledge_registry.recipes()
    }
    recipe_entries = {
        str(item.get("target") or "").split(":", 1)[1]: item
        for item in knowledge_registry.promotion_entries()
        if item.get("kind") == "recipe"
        and str(item.get("target") or "").startswith("recipe:")
    }

    issues = []

    for item in capability_guidance.catalog(promoted_only=True)["entries"]:
        capability = str(item.get("capability") or "")
        if capability and capability not in implemented:
            issues.append({
                "code": "GUIDANCE_CAPABILITY_MISSING",
                "id": item.get("id"),
                "capability": capability,
            })

    for recipe_id in sorted(set(recipes) - set(recipe_entries)):
        issues.append({
            "code": "RECIPE_LIFECYCLE_MISSING",
            "recipe": recipe_id,
        })
    for recipe_id in sorted(set(recipe_entries) - set(recipes)):
        issues.append({
            "code": "LIFECYCLE_RECIPE_MISSING",
            "recipe": recipe_id,
        })

    for recipe_id, entry in recipe_entries.items():
        if entry.get("state") != "deprecated":
            continue
        replacement = str(entry.get("superseded_by") or "")
        if not replacement:
            issues.append({
                "code": "DEPRECATED_REPLACEMENT_MISSING",
                "recipe": recipe_id,
            })
            continue
        replacement_row = recipes.get(replacement)
        if replacement_row is None or replacement_row.get("promotion_state") != "promoted":
            issues.append({
                "code": "DEPRECATED_REPLACEMENT_INVALID",
                "recipe": recipe_id,
                "superseded_by": replacement,
            })

    return {
        "ok": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "implemented_capability_count": len(implemented),
        "recipe_count": len(recipes),
        "recipe_lifecycle_count": len(recipe_entries),
    }


def capability_search(hou, session_info: dict, query: str, limit: int = 20) -> dict:
    from . import knowledge_registry

    query = str(query or "").strip()
    if not query:
        raise ValueError("ARGUMENT_REQUIRED: query")
    try:
        limit = max(1, min(int(limit), 100))
    except Exception:
        limit = 20

    guidance_by_capability = {
        str(item.get("capability") or ""): item
        for item in capability_guidance.catalog(promoted_only=False)["entries"]
        if str(item.get("capability") or "")
    }
    promotion_by_target = {
        str(item.get("target") or ""): item
        for item in knowledge_registry.promotion_entries()
        if str(item.get("target") or "")
    }

    results = []

    for raw in session_info.get("capabilities") or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or ""):
            continue
        name = str(raw["name"])
        guidance = guidance_by_capability.get(name)
        row = {
            "kind": "capability",
            "name": name,
            "availability": "implemented",
            "routable": True,
            "execution_policy": "subject_to_command_risk_and_execution_policy",
            "write": bool(raw.get("write", False)),
            "risk": raw.get("risk"),
            "version": raw.get("version"),
            "rollback": raw.get("rollback"),
            "verification": raw.get("verification"),
            "tested_host_versions": list(raw.get("tested_host_versions") or []),
            "argument_schema": copy.deepcopy(ARGUMENT_SCHEMAS.get(name)),
            "guidance": copy.deepcopy(guidance),
        }
        score = _capability_search_score(query, row, name)
        if score is not None:
            row["_search"] = score
            results.append(row)

    for summary in knowledge_registry.recipes():
        recipe_id = str(summary.get("id") or "")
        full = knowledge_registry.get_recipe(recipe_id)
        promotion = promotion_by_target.get(f"recipe:{recipe_id}")
        row = {
            "kind": "recipe",
            "name": recipe_id,
            "availability": "knowledge_pack",
            "promotion_state": summary.get("promotion_state"),
            "execution_authorized": bool(summary.get("execution_authorized")),
            "version": summary.get("version"),
            "description": summary.get("description"),
            "required": list(summary.get("required") or []),
            "optional": list(summary.get("optional") or []),
            "scope": full.get("scope"),
            "supported_host_versions": list(full.get("supported_host_versions") or []),
            "promotion": copy.deepcopy(promotion),
            "superseded_by": (
                (promotion or {}).get("superseded_by")
                or full.get("superseded_by")
            ),
        }
        score = _capability_search_score(query, row, recipe_id)
        if score is not None:
            row["_search"] = score
            results.append(row)

    results.sort(
        key=lambda item: (
            item.get("_search", {}).get("score", 0),
            bool(item.get("execution_authorized")),
            item.get("kind") == "capability",
        ),
        reverse=True,
    )
    selected = results[:limit]

    related = knowledge_registry.search(query, limit=min(limit, 20))
    return {
        "query": query,
        "count": len(selected),
        "total_matches": len(results),
        "matching": [
            "exact_name",
            "name_substring",
            "exact_substring",
            "partial_token_overlap",
        ],
        "results": selected,
        "related_knowledge": related["results"],
        "adapter": "houdini",
        "adapter_version": session_info.get("adapter_version"),
        "host_version": hou.applicationVersionString(),
        "project_file": hou.hipFile.path(),
        "integrity": capability_integrity(session_info),
    }


def hip_status(hou) -> dict:
    path = hou.hipFile.path()
    return {
        "path": path,
        "name": Path(path).name if path else "",
        "has_unsaved_changes": bool(_call(hou.hipFile, "hasUnsavedChanges", False)),
        "is_loading": bool(_call(hou.hipFile, "isLoadingHipFile", False)),
        "frame": hou.frame(),
        "fps": hou.fps(),
        "host_version": hou.applicationVersionString(),
    }


def hip_save(hou, path: str | None = None) -> dict:
    if path:
        hou.hipFile.save(file_name=str(path))
    else:
        hou.hipFile.save()
    return hip_status(hou)


def selection_get(hou) -> dict:
    return {"paths": [node.path() for node in hou.selectedNodes()]}


def selection_set(hou, paths: list[str], current: str | None = None) -> dict:
    clear = getattr(hou, "clearAllSelected", None)
    if callable(clear):
        clear()
    nodes = []
    for path in paths:
        node = _node(hou, path)
        node.setSelected(True, clear_all_selected=False)
        nodes.append(node)
    if current:
        _node(hou, current).setCurrent(True, clear_all_selected=False)
    return {"paths": [node.path() for node in hou.selectedNodes()]}


def node_state(hou, path: str) -> dict:
    node = _node(hou, path)
    node_type = node.type()
    pos = _call(node, "position")
    color = _call(node, "color")
    out = {
        "path": node.path(),
        "name": node.name(),
        "type": _call(node_type, "nameWithCategory", _call(node_type, "name")),
        "bypass": _call(node, "isBypassed"),
        "display": _call(node, "isDisplayFlagSet"),
        "render": _call(node, "isRenderFlagSet"),
        "template": _call(node, "isTemplateFlagSet"),
        "selectable": _call(node, "isSelectableInViewport"),
        "comment": _call(node, "comment"),
        "position": list(pos) if pos is not None else None,
        "color": list(color.rgb()) if color is not None and hasattr(color, "rgb") else None,
        "inputs": [n.path() if n is not None else None for n in node.inputs()],
        "outputs": [n.path() for n in node.outputs()],
        "errors": list(_call(node, "errors", ()) or ()),
        "warnings": list(_call(node, "warnings", ()) or ()),
    }
    return out


def node_set_state(hou, path: str, values: dict) -> dict:
    node = _node(hou, path)
    if "name" in values:
        node.setName(str(values["name"]), unique_name=bool(values.get("unique_name", True)))
    mapping = {
        "bypass": "bypass",
        "display": "setDisplayFlag",
        "render": "setRenderFlag",
        "template": "setTemplateFlag",
        "selectable": "setSelectableInViewport",
    }
    for key, method in mapping.items():
        if key in values:
            fn = getattr(node, method, None)
            if callable(fn):
                fn(bool(values[key]))
    if "position" in values:
        node.setPosition(values["position"])
    if "color" in values:
        node.setColor(hou.Color(tuple(values["color"])))
    if "comment" in values and hasattr(node, "setComment"):
        node.setComment(str(values["comment"]))
    return node_state(hou, node.path())


def _validate_batch_item(item, index: int, *, write: bool) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] must be an object")
    path = str(item.get("path") or "").strip()
    parameter = str(item.get("parameter") or "").strip()
    if not path or not parameter:
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] requires path and parameter")
    out = {"path": path, "parameter": parameter}
    if write:
        if "value" not in item:
            raise ValueError(f"ARGUMENT_INVALID: items[{index}] requires value")
        out["value"] = item["value"]
        if "expected_hash" in item:
            out["expected_hash"] = item.get("expected_hash")
    return out


def normalize_parm_batch_read_args(args: dict) -> tuple[list[dict], str]:
    args = args or {}
    if "items" in args:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")
        return [
            _validate_batch_item(item, index, write=False)
            for index, item in enumerate(items)
        ], "items"

    if "path" not in args and "parameters" not in args:
        raise ValueError("ARGUMENT_REQUIRED: items or path+parameters")

    path = str(args.get("path") or "").strip()
    parameters = args.get("parameters")
    if not path:
        raise ValueError("ARGUMENT_INVALID: path must be a non-empty string")
    if not isinstance(parameters, list) or not parameters:
        raise ValueError("ARGUMENT_INVALID: parameters must be a non-empty list")

    items = []
    for index, parameter in enumerate(parameters):
        name = str(parameter or "").strip()
        if not name:
            raise ValueError(f"ARGUMENT_INVALID: parameters[{index}] must be a non-empty string")
        items.append({"path": path, "parameter": name})
    return items, "path+parameters"


def normalize_parm_batch_write_args(args: dict) -> tuple[list[dict], str]:
    args = args or {}
    if "items" in args:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")
        return [
            _validate_batch_item(item, index, write=True)
            for index, item in enumerate(items)
        ], "items"

    if "path" not in args and "writes" not in args:
        raise ValueError("ARGUMENT_REQUIRED: items or path+writes")

    path = str(args.get("path") or "").strip()
    writes = args.get("writes")
    if not path:
        raise ValueError("ARGUMENT_INVALID: path must be a non-empty string")
    if not isinstance(writes, list) or not writes:
        raise ValueError("ARGUMENT_INVALID: writes must be a non-empty list")

    items = []
    for index, item in enumerate(writes):
        if not isinstance(item, dict):
            raise ValueError(f"ARGUMENT_INVALID: writes[{index}] must be an object")
        merged = dict(item)
        merged["path"] = path
        items.append(_validate_batch_item(merged, index, write=True))
    return items, "path+writes"


def parm_batch_read(hou, items: list[dict]) -> dict:
    from . import parm_ops
    if not items:
        raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")
    results = [parm_ops.read(hou, item["path"], item["parameter"]) for item in items]
    return {"items": results, "count": len(results)}


def parm_batch_write(hou, items: list[dict]) -> dict:
    from . import parm_ops
    if not items:
        raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")

    for index, item in enumerate(items):
        if item.get("expected_hash") is None:
            raise ValueError(f"EXPECTED_HASH_REQUIRED: items[{index}]")

    preflight = []
    conflicts = []
    for item in items:
        before = parm_ops.read(hou, item["path"], item["parameter"])
        preflight.append(before)
        if before["hash"] != item["expected_hash"]:
            conflicts.append({
                "path": item["path"],
                "parameter": item["parameter"],
                "expected_hash": item["expected_hash"],
                "actual_hash": before["hash"],
                "before": before,
            })

    if conflicts:
        return {
            "conflict": True,
            "written": 0,
            "conflicts": conflicts,
            "preflight": preflight,
            "items": [],
        }

    results = []
    for item in items:
        result = parm_ops.write(
            hou,
            item["path"],
            item["parameter"],
            item["value"],
            item["expected_hash"],
        )
        if isinstance(result, dict) and result.get("conflict"):
            return {
                "conflict": True,
                "written": len(results),
                "conflicts": [{
                    "path": item["path"],
                    "parameter": item["parameter"],
                    "late_conflict": True,
                    "result": result,
                }],
                "preflight": preflight,
                "items": results,
            }
        results.append(result)

    return {
        "conflict": False,
        "written": len(results),
        "preflight": preflight,
        "items": results,
    }
