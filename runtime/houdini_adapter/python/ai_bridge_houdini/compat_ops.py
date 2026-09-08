from __future__ import annotations

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
