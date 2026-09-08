from __future__ import annotations

import hashlib
import json


def _safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _node_type(node, *, compact: bool = False):
    node_type = node.type()
    category = node_type.category().name() if hasattr(node_type, "category") else None
    out = {"name": node_type.name(), "category": category}
    if not compact:
        out["name_with_category"] = node_type.nameWithCategory() if hasattr(node_type, "nameWithCategory") else node_type.name()
    return out


def _connections(node, *, compact: bool = False):
    inputs = []
    for c in node.inputConnections():
        n = c.inputNode()
        item = {"from": None if n is None else n.path(), "input_index": c.inputIndex(), "output_index": c.outputIndex()}
        if not compact:
            item["to"] = node.path()
        inputs.append(item)
    outputs = []
    for c in node.outputConnections():
        n = c.outputNode()
        item = {"to": None if n is None else n.path(), "input_index": c.inputIndex(), "output_index": c.outputIndex()}
        if not compact:
            item["from"] = node.path()
        outputs.append(item)
    return inputs, outputs


def _parameter_schema(node):
    result = []
    for parm in node.parms():
        template = parm.parmTemplate()
        type_obj = template.type()
        type_name = type_obj.name() if hasattr(type_obj, "name") else str(type_obj)
        result.append({"name": parm.name(), "type": type_name})
    return result


def _parameters_deep(node):
    result = []
    for parm in node.parms():
        template = parm.parmTemplate()
        type_obj = template.type()
        type_name = type_obj.name() if hasattr(type_obj, "name") else str(type_obj)
        try:
            value = parm.eval()
            eval_error = None
        except Exception as exc:
            value = None
            eval_error = f"{type(exc).__name__}: {exc}"
        result.append({"name": parm.name(), "type": type_name, "value": _safe(value), "eval_error": eval_error})
    return result


def _snippet_meta(node):
    parm = node.parm("snippet")
    if parm is None:
        return None
    try:
        text = parm.evalAsString()
        return {"chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def inspect_context(hou):
    update_mode = None
    try:
        mode = hou.updateModeSetting()
        update_mode = mode.name() if hasattr(mode, "name") else str(mode)
    except Exception:
        pass
    return {
        "hip_path": hou.hipFile.path(), "frame": hou.frame(),
        "selected_nodes": [n.path() for n in hou.selectedNodes()],
        "host_version": hou.applicationVersionString(),
        "ui_available": bool(hou.isUIAvailable()), "update_mode": update_mode,
    }


def inspect_node(hou, path: str, mode: str = "deep"):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    mode = str(mode or "normal").lower()
    if mode not in {"summary", "normal", "deep"}:
        raise ValueError("mode must be summary, normal, or deep")
    inputs, outputs = _connections(node, compact=(mode == "summary"))
    errors = list(node.errors())
    warnings = list(node.warnings())
    if mode == "summary":
        return {
            "path": node.path(), "name": node.name(), "type": _node_type(node, compact=True),
            "inputs": inputs, "outputs": outputs,
            "error_count": len(errors), "warning_count": len(warnings),
            "parameter_count": len(node.parms()), "snippet": _snippet_meta(node),
        }
    data = {
        "path": node.path(), "name": node.name(), "type": _node_type(node),
        "inputs": inputs, "outputs": outputs,
        "errors": errors, "warnings": warnings,
        "messages": list(node.messages()) if hasattr(node, "messages") else [],
    }
    if mode == "normal":
        data["parameters"] = _parameter_schema(node)
        data["snippet"] = _snippet_meta(node)
        return data
    data["parameters"] = _parameters_deep(node)
    snippet = node.parm("snippet")
    if snippet is not None:
        try:
            data["snippet"] = snippet.evalAsString()
        except Exception as exc:
            data["snippet_error"] = f"{type(exc).__name__}: {exc}"
    return data


def inspect_network(hou, path: str, depth: int = 1, mode: str = "summary", max_nodes: int = 500):
    root = hou.node(path)
    if root is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    if depth < 0:
        raise ValueError("depth must be >= 0")
    max_nodes = max(1, min(int(max_nodes), 5000))
    result = []
    truncated = False
    def visit(node, level):
        nonlocal truncated
        if len(result) >= max_nodes:
            truncated = True; return
        result.append(inspect_node(hou, node.path(), mode=mode))
        if level >= depth: return
        for child in node.children():
            visit(child, level + 1)
            if truncated: return
    visit(root, 0)
    return {"root": path, "depth": depth, "mode": mode, "node_count": len(result), "truncated": truncated, "nodes": result}


def inspect_find(hou, root: str, *, node_type: str | None = None, name_contains: str | None = None, max_results: int = 100):
    base = hou.node(root)
    if base is None:
        raise ValueError(f"NODE_NOT_FOUND: {root}")
    needle = (name_contains or "").lower()
    max_results = max(1, min(int(max_results), 1000))
    out = []
    stack = [base]
    while stack and len(out) < max_results:
        node = stack.pop()
        type_name = node.type().name()
        if (node_type is None or type_name == node_type) and (not needle or needle in node.name().lower()):
            out.append(inspect_node(hou, node.path(), mode="summary"))
        children = list(node.children())
        stack.extend(reversed(children))
    return {"root": root, "node_type": node_type, "name_contains": name_contains, "count": len(out), "max_results": max_results, "nodes": out}

def _normalize_batch_node_paths(paths) -> list[str]:
    if not isinstance(paths, list) or not paths:
        raise ValueError("ARGUMENT_INVALID: paths must be a non-empty list")
    if len(paths) > 1000:
        raise ValueError("ARGUMENT_INVALID: paths exceeds maximum batch size 1000")
    out = []
    for index, value in enumerate(paths):
        path = str(value or "").strip()
        if not path:
            raise ValueError(f"ARGUMENT_INVALID: paths[{index}] must be a non-empty string")
        out.append(path)
    return out


def _filter_node_fields(data: dict, fields):
    if fields is None:
        return data
    if not isinstance(fields, list):
        raise ValueError("ARGUMENT_INVALID: fields must be a list of strings")
    normalized = []
    for index, value in enumerate(fields):
        name = str(value or "").strip()
        if not name:
            raise ValueError(f"ARGUMENT_INVALID: fields[{index}] must be a non-empty string")
        if name not in normalized:
            normalized.append(name)
    return {name: data[name] for name in normalized if name in data}


def inspect_batch_nodes(
    hou,
    paths,
    mode: str = "summary",
    max_details: int = 200,
    fields=None,
):
    paths = _normalize_batch_node_paths(paths)
    mode = str(mode or "summary").lower()
    if mode not in {"summary", "normal", "deep"}:
        raise ValueError("ARGUMENT_INVALID: mode must be summary, normal, or deep")
    max_details = max(0, min(int(max_details), 1000))
    if fields is not None and not isinstance(fields, list):
        raise ValueError("ARGUMENT_INVALID: fields must be a list of strings")

    items = []
    succeeded = 0
    failed = 0
    detailed = 0
    omitted = 0

    for path in paths:
        try:
            data = inspect_node(hou, path, mode=mode)
            succeeded += 1
            include_detail = detailed < max_details
            if include_detail:
                data = _filter_node_fields(data, fields)
                detailed += 1
            else:
                data = None
                omitted += 1
            item = {
                "path": path,
                "success": True,
                "data": data,
                "error": None,
            }
            if not include_detail:
                item["detail_omitted"] = True
            items.append(item)
        except Exception as exc:
            failed += 1
            message = str(exc)
            code = "NODE_NOT_FOUND" if message.startswith("NODE_NOT_FOUND:") else "INSPECT_NODE_FAILED"
            items.append({
                "path": path,
                "success": False,
                "data": None,
                "error": {
                    "code": code,
                    "message": message,
                    "exception_type": type(exc).__name__,
                },
            })

    return {
        "requested": len(paths),
        "succeeded": succeeded,
        "failed": failed,
        "mode": mode,
        "fields": None if fields is None else [str(value) for value in fields],
        "max_details": max_details,
        "detailed": detailed,
        "omitted": omitted,
        "truncated": omitted > 0,
        "items": items,
    }
