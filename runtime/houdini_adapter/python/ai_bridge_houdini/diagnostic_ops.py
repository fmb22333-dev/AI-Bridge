from __future__ import annotations

import re

from . import cook_ops, geometry_ops, graph_ops


_MARKER_KEY = "ai_bridge_diagnostic_id"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return node


def _diagnostic_id(value) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError("ARGUMENT_REQUIRED: diagnostic_id")
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("ARGUMENT_INVALID: diagnostic_id must match [A-Za-z0-9._-]{1,128}")
    return value


def _namespace(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "diag"
    return "__AI_DIAG_" + token[:48] + "_"


def _cleanup_paths(hou, paths: list[str]) -> dict:
    destroyed = []
    errors = []
    for path in reversed(list(paths or [])):
        try:
            node = hou.node(path)
            if node is not None:
                node.destroy()
            if hou.node(path) is None:
                destroyed.append(path)
            else:
                errors.append({"path": path, "message": "node still exists after destroy"})
        except Exception as exc:
            errors.append({
                "path": path,
                "exception_type": type(exc).__name__,
                "message": str(exc),
            })
    return {
        "verified": not errors,
        "destroyed": destroyed,
        "errors": errors,
    }


def _marked_children(parent, diagnostic_id: str):
    children_fn = getattr(parent, "children", None)
    if not callable(children_fn):
        raise RuntimeError("DIAGNOSTIC_CLEANUP_UNAVAILABLE: parent.children is unavailable")
    matches = []
    for node in children_fn() or []:
        user_data = getattr(node, "userData", None)
        if not callable(user_data):
            raise RuntimeError("DIAGNOSTIC_CLEANUP_UNAVAILABLE: node.userData is unavailable")
        try:
            marker = user_data(_MARKER_KEY)
        except Exception as exc:
            raise RuntimeError(f"DIAGNOSTIC_CLEANUP_UNAVAILABLE: {type(exc).__name__}: {exc}") from exc
        if marker == diagnostic_id:
            matches.append(node.path())
    return matches


def cleanup(hou, parent_path: str, diagnostic_id: str) -> dict:
    diagnostic_id = _diagnostic_id(diagnostic_id)
    parent = _node(hou, str(parent_path))
    paths = _marked_children(parent, diagnostic_id)
    result = _cleanup_paths(hou, paths)
    return {
        "mode": "cleanup",
        "diagnostic_id": diagnostic_id,
        "parent": parent.path(),
        "matched": paths,
        "cleanup": result,
        "verified": result["verified"],
    }


def _normalize_nodes(parent, nodes, diagnostic_id: str):
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("ARGUMENT_INVALID: nodes must be a non-empty list")
    if len(nodes) > 32:
        raise ValueError("ARGUMENT_INVALID: nodes exceeds maximum 32")

    prefix = _namespace(diagnostic_id)
    logical_refs = set()
    prepared = []
    actual_names = []

    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise ValueError(f"ARGUMENT_INVALID: nodes[{index}] must be an object")
        ref = str(raw.get("id") or raw.get("name") or f"node_{index}").strip()
        if not ref:
            raise ValueError(f"ARGUMENT_INVALID: nodes[{index}] requires id/name")
        if ref in logical_refs:
            raise ValueError(f"ARGUMENT_INVALID: duplicate node ref {ref}")
        logical_refs.add(ref)

        node_type = str(raw.get("type") or raw.get("node_type") or "").strip()
        if not node_type:
            raise ValueError(f"ARGUMENT_INVALID: nodes[{index}] requires type")

        base = str(raw.get("name") or ref)
        base = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_") or f"node_{index}"
        actual_name = (prefix + base)[:120]
        if actual_name in actual_names:
            raise ValueError(f"ARGUMENT_INVALID: duplicate generated node name {actual_name}")
        actual_names.append(actual_name)

        if parent.node(actual_name) is not None:
            return None, logical_refs, {
                "conflict": True,
                "reason": "DIAGNOSTIC_NAMESPACE_OCCUPIED",
                "diagnostic_id": diagnostic_id,
                "existing": [parent.node(actual_name).path()],
            }

        parms = raw.get("parms") or {}
        state = raw.get("state") or {}
        if not isinstance(parms, dict) or len(parms) > 64:
            raise ValueError(f"ARGUMENT_INVALID: nodes[{index}].parms must be object <=64")
        if not isinstance(state, dict):
            raise ValueError(f"ARGUMENT_INVALID: nodes[{index}].state must be object")

        prepared.append({
            "id": ref,
            "name": actual_name,
            "type": node_type,
            "parms": dict(parms),
            "state": dict(state),
        })

    return prepared, logical_refs, None


def _normalize_connections(hou, logical_refs: set[str], connections):
    if connections is None:
        return []
    if not isinstance(connections, list):
        raise ValueError("ARGUMENT_INVALID: connections must be a list")
    if len(connections) > 64:
        raise ValueError("ARGUMENT_INVALID: connections exceeds maximum 64")

    out = []
    for index, raw in enumerate(connections):
        if not isinstance(raw, dict):
            raise ValueError(f"ARGUMENT_INVALID: connections[{index}] must be an object")
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        if target not in logical_refs:
            raise ValueError(
                f"DIAGNOSTIC_EXTERNAL_TARGET_FORBIDDEN: connections[{index}] target must be a temporary node ref"
            )
        if source not in logical_refs:
            if not source.startswith("/") or hou.node(source) is None:
                raise ValueError(
                    f"DIAGNOSTIC_SOURCE_NOT_FOUND: connections[{index}] source {source!r}"
                )
        try:
            input_index = int(raw.get("input", raw.get("input_index", 0)))
            output_index = int(raw.get("output", raw.get("output_index", 0)))
        except Exception as exc:
            raise ValueError(f"ARGUMENT_INVALID: connections[{index}] invalid index") from exc
        if input_index < 0 or output_index < 0:
            raise ValueError(f"ARGUMENT_INVALID: connections[{index}] indices must be non-negative")
        out.append({
            "source": source,
            "target": target,
            "input": input_index,
            "output": output_index,
        })
    return out


def _normalize_collect(collect):
    if collect is None:
        return []
    if not isinstance(collect, list):
        raise ValueError("ARGUMENT_INVALID: collect must be a list")
    if len(collect) > 16:
        raise ValueError("ARGUMENT_INVALID: collect exceeds maximum 16")
    out = []
    for index, raw in enumerate(collect):
        if not isinstance(raw, dict):
            raise ValueError(f"ARGUMENT_INVALID: collect[{index}] must be an object")
        ref = str(raw.get("path") or raw.get("ref") or "").strip()
        if not ref:
            raise ValueError(f"ARGUMENT_INVALID: collect[{index}] requires path/ref")
        item = dict(raw)
        item["ref"] = ref
        item.pop("path", None)
        out.append(item)
    return out


def transaction(
    hou,
    parent_path: str,
    *,
    diagnostic_id: str,
    nodes=None,
    connections=None,
    cook_ref: str | None = None,
    collect=None,
    mode: str = "run",
) -> dict:
    mode = str(mode or "run").strip().lower()
    if mode == "cleanup":
        return cleanup(hou, parent_path, diagnostic_id)
    if mode != "run":
        raise ValueError("ARGUMENT_INVALID: mode must be run|cleanup")

    diagnostic_id = _diagnostic_id(diagnostic_id)
    parent = _node(hou, str(parent_path))

    prepared_nodes, logical_refs, conflict = _normalize_nodes(parent, nodes, diagnostic_id)
    if conflict is not None:
        return conflict

    connections = _normalize_connections(hou, logical_refs, connections)
    collect = _normalize_collect(collect)

    cook_ref = str(cook_ref or "").strip()
    if not cook_ref or cook_ref not in logical_refs:
        raise ValueError("ARGUMENT_INVALID: cook_ref must reference a temporary node id")

    for index, item in enumerate(collect):
        if item["ref"] not in logical_refs:
            raise ValueError(f"ARGUMENT_INVALID: collect[{index}] ref must be a temporary node id")

    created = []
    refs = {}
    cook_result = None
    collections = []
    primary_failure = None

    try:
        apply_result = graph_ops.apply_spec(
            hou,
            parent.path(),
            prepared_nodes,
            connections,
            layout=False,
            allow_update_existing=False,
        )
        created = list(apply_result.get("created") or [])
        refs = dict(apply_result.get("refs") or {})

        # Mark every created node before Cook so an unknown/timeout lifecycle can
        # be recovered later through mode=cleanup + diagnostic_id.
        for path in created:
            node = _node(hou, path)
            setter = getattr(node, "setUserData", None)
            if not callable(setter):
                raise RuntimeError("DIAGNOSTIC_MARKER_UNAVAILABLE: node.setUserData is unavailable")
            setter(_MARKER_KEY, diagnostic_id)

        cook_path = refs[cook_ref]
        cook_result = cook_ops.cook(hou, cook_path, force=True)
        if cook_result.get("cook_status") != "PASS":
            primary_failure = {
                "code": "DIAGNOSTIC_COOK_FAILED",
                "category": "cook",
                "message": "Temporary diagnostic Cook returned errors.",
                "retryable": False,
                "host_errors": list(cook_result.get("errors") or []),
                "host_warnings": list(cook_result.get("warnings") or []),
            }
        else:
            for index, item in enumerate(collect):
                ref = item["ref"]
                try:
                    result = geometry_ops.query(
                        hou,
                        refs[ref],
                        owner=item.get("owner", "point"),
                        attributes=item.get("attributes"),
                        key_attribute=item.get("key_attribute"),
                        key_values=item.get("key_values"),
                        mode=item.get("mode", "summary"),
                        frames=item.get("frames"),
                        max_rows=item.get("max_rows", 200),
                        max_attributes=item.get("max_attributes", 32),
                    )
                    collections.append({
                        "index": index,
                        "ref": ref,
                        "path": refs[ref],
                        "result": result,
                    })
                except Exception as exc:
                    primary_failure = {
                        "code": "DIAGNOSTIC_COLLECTION_FAILED",
                        "category": "collection",
                        "message": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                        "collect_index": index,
                        "ref": ref,
                    }
                    break
    except Exception as exc:
        primary_failure = {
            "code": "DIAGNOSTIC_TRANSACTION_FAILED",
            "category": "transaction",
            "message": f"{type(exc).__name__}: {exc}",
            "retryable": False,
        }
    finally:
        cleanup_result = _cleanup_paths(hou, created)

    if not cleanup_result["verified"]:
        return {
            "verified": False,
            "mode": "run",
            "diagnostic_id": diagnostic_id,
            "namespace": _namespace(diagnostic_id),
            "parent": parent.path(),
            "created": created,
            "refs": refs,
            "cook": cook_result,
            "collections": collections,
            "cleanup": cleanup_result,
            "failure": {
                "code": "DIAGNOSTIC_CLEANUP_FAILED",
                "category": "cleanup",
                "message": "One or more temporary diagnostic nodes remain after cleanup.",
                "retryable": False,
                "underlying": primary_failure,
                "recovery": {
                    "operation": "diagnostic.transaction",
                    "mode": "cleanup",
                    "diagnostic_id": diagnostic_id,
                    "parent": parent.path(),
                },
            },
        }

    if primary_failure is not None:
        return {
            "verified": False,
            "mode": "run",
            "diagnostic_id": diagnostic_id,
            "namespace": _namespace(diagnostic_id),
            "parent": parent.path(),
            "created": created,
            "refs": refs,
            "cook": cook_result,
            "collections": collections,
            "cleanup": cleanup_result,
            "failure": primary_failure,
        }

    return {
        "verified": True,
        "mode": "run",
        "diagnostic_id": diagnostic_id,
        "namespace": _namespace(diagnostic_id),
        "parent": parent.path(),
        "created": created,
        "refs": refs,
        "cook": cook_result,
        "collections": collections,
        "cleanup": cleanup_result,
    }
