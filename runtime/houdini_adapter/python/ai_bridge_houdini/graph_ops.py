from __future__ import annotations

from typing import Any

from .hashing import stable_hash


def _node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"Node not found: {path}")
    return node


def _type_name(node) -> str:
    node_type = node.type()
    fn = getattr(node_type, "nameWithCategory", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    return node_type.name()


def _find_type(parent, node_type: str):
    category = parent.childTypeCategory()
    candidates = category.nodeTypes()
    if node_type in candidates:
        return candidates[node_type]
    matches = [value for key, value in candidates.items() if key.split("::")[0] == node_type]
    return matches[0] if len(matches) == 1 else None


def _parm_names(node_type_obj) -> set[str]:
    if node_type_obj is None:
        return set()
    try:
        group = node_type_obj.parmTemplateGroup()
        entries = group.entriesWithoutFolders()
        return {entry.name() for entry in entries}
    except Exception:
        return set()


def _normalize_state(spec: dict) -> dict:
    allowed = {"position","comment","display","render","template","selectable","bypass","color"}
    return {key: value for key, value in spec.items() if key in allowed}


def validate_spec(hou, parent_path: str, nodes: list[dict], connections: list[dict] | None = None) -> dict:
    parent = _node(hou, parent_path)
    errors: list[dict] = []
    refs: dict[str, dict] = {}
    names: set[str] = set()

    for index, spec in enumerate(nodes):
        ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
        name = str(spec.get("name") or ref)
        node_type = str(spec.get("type") or spec.get("node_type") or "")
        if not node_type:
            errors.append({"code":"NODE_TYPE_REQUIRED","ref":ref,"index":index})
            continue
        if name in names:
            errors.append({"code":"DUPLICATE_NODE_NAME","ref":ref,"name":name})
        names.add(name)

        existing = parent.node(name)
        type_obj = _find_type(parent, node_type)
        if existing is None and type_obj is None:
            errors.append({"code":"NODE_TYPE_NOT_FOUND","ref":ref,"node_type":node_type,"parent":parent_path})
            continue
        if existing is not None and existing.type().name() != node_type and not bool(spec.get("allow_existing_type_mismatch", False)):
            errors.append({
                "code":"NODE_TYPE_MISMATCH",
                "ref":ref,
                "path":existing.path(),
                "expected":node_type,
                "actual":existing.type().name(),
            })

        parms = dict(spec.get("parms") or {})
        parm_names = _parm_names(type_obj if type_obj is not None else existing.type())
        for parm_name in parms:
            if parm_names and parm_name not in parm_names:
                errors.append({"code":"PARM_NOT_FOUND","ref":ref,"parameter":parm_name,"node_type":node_type})
        refs[ref]={"name":name,"type":node_type,"existing":existing.path() if existing else None}

    for index, connection in enumerate(connections or []):
        source=str(connection.get("source") or "")
        target=str(connection.get("target") or "")
        if source not in refs and hou.node(source) is None:
            errors.append({"code":"SOURCE_NOT_FOUND","index":index,"source":source})
        if target not in refs and hou.node(target) is None:
            errors.append({"code":"TARGET_NOT_FOUND","index":index,"target":target})
        try:
            input_index=int(connection.get("input",connection.get("input_index",0)))
            output_index=int(connection.get("output",connection.get("output_index",0)))
            if input_index < 0 or output_index < 0:
                raise ValueError
        except Exception:
            errors.append({"code":"INVALID_CONNECTION_INDEX","index":index})

    return {
        "ok":not errors,
        "parent":parent.path(),
        "node_count":len(nodes),
        "connection_count":len(connections or []),
        "refs":refs,
        "errors":errors,
        "warnings":[],
    }


def _resolve(hou, parent, refs: dict[str, Any], value: str):
    if value in refs:
        return refs[value]
    if value.startswith("/"):
        return _node(hou,value)
    candidate=parent.node(value)
    if candidate is not None:
        return candidate
    raise ValueError(f"Node reference not found: {value}")


def _set_parms(node, parms: dict) -> list[str]:
    changed=[]
    for name,value in parms.items():
        parm=node.parm(name)
        if parm is None:
            raise ValueError(f"Parameter not found: {node.path()}::{name}")
        parm.set(value)
        changed.append(name)
    return changed


def _set_state(hou,node,state:dict) -> list[str]:
    changed=[]
    values=_normalize_state(state)
    mapping={"bypass":"bypass","display":"setDisplayFlag","render":"setRenderFlag","template":"setTemplateFlag","selectable":"setSelectableInViewport"}
    for key,method in mapping.items():
        if key in values:
            fn=getattr(node,method,None)
            if callable(fn):
                fn(bool(values[key]))
                changed.append(key)
    if "position" in values:
        node.setPosition(values["position"]); changed.append("position")
    if "color" in values:
        node.setColor(hou.Color(tuple(values["color"]))); changed.append("color")
    if "comment" in values and hasattr(node,"setComment"):
        node.setComment(str(values["comment"])); changed.append("comment")
    return changed


def create_configured(hou,parent_path:str,node_type:str,name:str,parms:dict|None=None,state:dict|None=None,inputs:list[dict|str|None]|None=None) -> dict:
    parent=_node(hou,parent_path)
    if parent.node(name) is not None:
        raise ValueError(f"Node already exists: {parent.path()}/{name}")
    preflight=validate_spec(hou,parent_path,[{"id":"node","name":name,"type":node_type,"parms":parms or {}}],[])
    if not preflight["ok"]:
        first=preflight["errors"][0]
        raise ValueError(f"{first['code']}: {first}")
    node=parent.createNode(node_type,node_name=name)
    try:
        changed_parms=_set_parms(node,dict(parms or {}))
        changed_state=_set_state(hou,node,dict(state or {}))
        connected=[]
        for input_index,source_spec in enumerate(inputs or []):
            if source_spec is None:
                continue
            if isinstance(source_spec,str):
                source=_resolve(hou,parent,{},source_spec); output_index=0
            else:
                source=_resolve(hou,parent,{},str(source_spec["source"]))
                output_index=int(source_spec.get("output",source_spec.get("output_index",0)))
            node.setInput(input_index,source,output_index)
            connected.append({"input":input_index,"source":source.path(),"output":output_index})
        return {
            "path":node.path(),
            "name":node.name(),
            "type":_type_name(node),
            "parms_set":changed_parms,
            "state_set":changed_state,
            "inputs":connected,
            "verified":hou.node(node.path()) is not None,
        }
    except Exception:
        try: node.destroy()
        except Exception: pass
        raise


def apply_spec(hou,parent_path:str,nodes:list[dict],connections:list[dict]|None=None,*,layout:bool=False,allow_update_existing:bool=False) -> dict:
    preflight=validate_spec(hou,parent_path,nodes,connections)
    if not preflight["ok"]:
        first=preflight["errors"][0]
        raise ValueError(f"{first['code']}: {first}")
    parent=_node(hou,parent_path)
    refs={}
    created=[]
    updated=[]
    created_nodes=[]
    try:
        for index,spec in enumerate(nodes):
            ref=str(spec.get("id") or spec.get("name") or f"node_{index}")
            name=str(spec.get("name") or ref)
            node_type=str(spec.get("type") or spec.get("node_type"))
            node=parent.node(name)
            if node is None:
                node=parent.createNode(node_type,node_name=name)
                created_nodes.append(node); created.append(node.path())
                _set_parms(node,dict(spec.get("parms") or {}))
                _set_state(hou,node,dict(spec.get("state") or {}))
            elif allow_update_existing:
                _set_parms(node,dict(spec.get("parms") or {}))
                _set_state(hou,node,dict(spec.get("state") or {}))
                updated.append(node.path())
            refs[ref]=node
        applied=[]
        for connection in connections or []:
            source=_resolve(hou,parent,refs,str(connection["source"]))
            target=_resolve(hou,parent,refs,str(connection["target"]))
            input_index=int(connection.get("input",connection.get("input_index",0)))
            output_index=int(connection.get("output",connection.get("output_index",0)))
            target.setInput(input_index,source,output_index)
            applied.append({"source":source.path(),"target":target.path(),"input":input_index,"output":output_index})
        if layout and hasattr(parent,"layoutChildren"):
            parent.layoutChildren(tuple(refs.values()))
        return {
            "parent":parent.path(),
            "created":created,
            "updated":updated,
            "connections":applied,
            "refs":{key:node.path() for key,node in refs.items()},
            "verified":all(hou.node(path) is not None for path in created),
        }
    except Exception:
        for node in reversed(created_nodes):
            try:
                if hou.node(node.path()) is not None:
                    node.destroy()
            except Exception:
                pass
        raise


def _transaction_refs(nodes):
    refs = set()
    for index, spec in enumerate(nodes):
        refs.add(str(spec.get("id") or spec.get("name") or f"node_{index}"))
    return refs


def _cleanup_created(hou, paths):
    errors = []
    destroyed = []
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
            errors.append({"path": path, "message": str(exc)})
    return {"verified": not errors, "destroyed": destroyed, "errors": errors}


def apply_transactional(hou, parent_path, nodes, connections=None, *, layout=False):
    from . import node_ops

    nodes = list(nodes or [])
    connections = list(connections or [])
    preflight = validate_spec(hou, parent_path, nodes, connections)
    if not preflight.get("ok"):
        return {
            "conflict": False, "verified": False, "created": [],
            "written_external_connections": 0,
            "errors": list(preflight.get("errors") or []), "preflight": preflight,
        }

    refs = _transaction_refs(nodes)
    internal = []
    external = []
    seen_external = set()

    for index, connection in enumerate(connections):
        target = str(connection.get("target") or "")
        if target in refs:
            internal.append(dict(connection))
            continue

        try:
            input_index = int(connection.get("input", connection.get("input_index", 0)))
        except Exception:
            return {
                "conflict": False, "verified": False, "created": [],
                "written_external_connections": 0,
                "errors": [{"code": "INVALID_CONNECTION_INDEX", "index": index}],
                "preflight": preflight,
            }

        expected_hash = connection.get("expected_hash")
        if expected_hash is None:
            return {
                "conflict": False, "verified": False, "created": [],
                "written_external_connections": 0,
                "errors": [{"code": "EXPECTED_HASH_REQUIRED", "index": index, "target": target, "input_index": input_index}],
                "preflight": preflight,
            }

        key = (target, input_index)
        if key in seen_external:
            return {
                "conflict": False, "verified": False, "created": [],
                "written_external_connections": 0,
                "errors": [{"code": "ARGUMENT_INVALID", "message": f"duplicate external target input {target}[{input_index}]"}],
                "preflight": preflight,
            }
        seen_external.add(key)
        external.append(dict(connection))

    external_states = []
    conflicts = []
    for connection in external:
        target = str(connection["target"])
        input_index = int(connection.get("input", connection.get("input_index", 0)))
        before = node_ops.input_state(hou, target, input_index)
        external_states.append(before)
        if before["hash"] != connection["expected_hash"]:
            conflicts.append({
                "target": target, "input_index": input_index,
                "expected_hash": connection["expected_hash"],
                "actual_hash": before["hash"], "before": before,
            })

    if conflicts:
        return {
            "conflict": True, "verified": False, "created": [],
            "written_external_connections": 0, "conflicts": conflicts,
            "preflight": preflight, "external_preflight": external_states,
        }

    apply_result = apply_spec(
        hou, parent_path, nodes, internal,
        layout=layout, allow_update_existing=False,
    )
    created = list(apply_result.get("created") or [])
    ref_paths = dict(apply_result.get("refs") or {})

    batch_items = []
    for connection in external:
        source_ref = str(connection.get("source") or "")
        batch_items.append({
            "target": str(connection["target"]),
            "input_index": int(connection.get("input", connection.get("input_index", 0))),
            "source": ref_paths.get(source_ref, source_ref),
            "output_index": int(connection.get("output", connection.get("output_index", 0))),
            "expected_hash": connection["expected_hash"],
        })

    if batch_items:
        batch = node_ops.batch_connect(hou, batch_items)
        if batch.get("conflict") or batch.get("verified") is False:
            cleanup = _cleanup_created(hou, created)
            return {
                "conflict": bool(batch.get("conflict")), "verified": False,
                "created": [], "written_external_connections": 0,
                "preflight": preflight, "external_preflight": external_states,
                "apply": apply_result, "batch": batch, "cleanup": cleanup,
            }
    else:
        batch = {"conflict": False, "written": 0, "verified": True, "items": []}

    return {
        "conflict": False,
        "verified": bool(apply_result.get("verified")) and bool(batch.get("verified")),
        "created": created, "refs": ref_paths,
        "internal_connections": list(apply_result.get("connections") or []),
        "written_external_connections": int(batch.get("written") or 0),
        "external_connections": list(batch.get("items") or []),
        "preflight": preflight, "external_preflight": external_states, "batch": batch,
    }


def _plain_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    rgb = getattr(value, "rgb", None)
    if callable(rgb):
        try:
            return [_plain_value(item) for item in rgb()]
        except Exception:
            pass
    try:
        return [_plain_value(item) for item in value]
    except Exception:
        return value


def _state_value(node, key):
    getters = {
        "bypass": "isBypassed",
        "display": "isDisplayFlagSet",
        "render": "isRenderFlagSet",
        "template": "isTemplateFlagSet",
        "selectable": "isSelectableInViewport",
        "comment": "comment",
        "position": "position",
        "color": "color",
    }
    method = getters.get(key)
    if method is None:
        raise ValueError(f"UNSUPPORTED_NODE_STATE: {key}")
    fn = getattr(node, method, None)
    if not callable(fn):
        raise ValueError(f"NODE_STATE_UNAVAILABLE: {node.path()}::{key}")
    return _plain_value(fn())


def _state_snapshot(node, desired):
    return {key: _state_value(node, key) for key in _normalize_state(desired)}


def _values_equal(left, right):
    return _plain_value(left) == _plain_value(right)


def _restore_ensure_transaction(hou, node_snapshots, connection_snapshots, created):
    from . import node_ops

    errors = []
    connection_restore = node_ops._restore_states(hou, connection_snapshots)
    if not connection_restore.get("verified"):
        errors.extend(connection_restore.get("errors") or [])

    for snapshot in reversed(node_snapshots):
        path = snapshot["path"]
        node = hou.node(path)
        if node is None:
            errors.append({"path": path, "message": "existing node missing during rollback"})
            continue
        try:
            for name, value in snapshot["parms"].items():
                parm = node.parm(name)
                if parm is None:
                    raise ValueError(f"Parameter not found during rollback: {path}::{name}")
                parm.set(value)
            _set_state(hou, node, snapshot["state"])
        except Exception as exc:
            errors.append({"path": path, "message": str(exc)})

    cleanup = _cleanup_created(hou, created)
    if not cleanup.get("verified"):
        errors.extend(cleanup.get("errors") or [])

    if not errors:
        for snapshot in node_snapshots:
            node = hou.node(snapshot["path"])
            if node is None:
                errors.append({"path": snapshot["path"], "message": "rollback verification node missing"})
                continue
            for name, value in snapshot["parms"].items():
                parm = node.parm(name)
                if parm is None or not _values_equal(parm.eval(), value):
                    errors.append({"path": snapshot["path"], "parameter": name, "message": "rollback parameter mismatch"})
            for key, value in snapshot["state"].items():
                if not _values_equal(_state_value(node, key), value):
                    errors.append({"path": snapshot["path"], "state": key, "message": "rollback state mismatch"})

    return {
        "verified": not errors,
        "errors": errors,
        "connections": connection_restore,
        "cleanup": cleanup,
    }


def _ensure_plan_summary(*, would_cook=False):
    return {
        "create_nodes": 0,
        "update_nodes": 0,
        "parameter_changes": 0,
        "state_changes": 0,
        "connection_changes": 0,
        "noop_nodes": 0,
        "noop_connections": 0,
        "noop_total": 0,
        "total_changes": 0,
        "would_cook": bool(would_cook),
    }


def _ensure_plan_compact(summary):
    return (
        f"CREATE {summary['create_nodes']} / "
        f"UPDATE_PARMS {summary['parameter_changes']} / "
        f"UPDATE_STATE {summary['state_changes']} / "
        f"CONNECT {summary['connection_changes']} / "
        f"NOOP {summary['noop_total']}"
    )


def _ensure_plan_path(hou, parent, ref_paths, value):
    value = str(value or "")
    if value in ref_paths:
        return ref_paths[value]
    if value.startswith("/"):
        return value
    candidate = parent.node(value)
    if candidate is not None:
        return candidate.path()
    return parent.path().rstrip("/") + "/" + value


def _ensure_plan_error(preflight, errors, *, cook_path=None):
    summary = _ensure_plan_summary(would_cook=bool(cook_path))
    return {
        "ok": False,
        "verified": False,
        "read_only": True,
        "advisory": True,
        "summary": summary,
        "compact": _ensure_plan_compact(summary),
        "nodes": [],
        "connections": [],
        "cook": {
            "requested": bool(cook_path),
            "target": None,
            "executed": False,
        },
        "detail_total": 0,
        "detail_returned": 0,
        "truncated": False,
        "errors": list(errors or []),
        "warnings": list((preflight or {}).get("warnings") or []),
        "preflight": preflight,
        "stale_between_plan_and_apply_possible": True,
    }


def plan_ensure(
    hou,
    parent_path,
    nodes,
    connections=None,
    *,
    cook_path=None,
    max_details=200,
):
    from . import node_ops

    nodes = list(nodes or [])
    connections = list(connections or [])
    max_details = max(0, min(int(max_details), 1000))
    preflight = validate_spec(hou, parent_path, nodes, connections)
    if not preflight.get("ok"):
        return _ensure_plan_error(
            preflight,
            list(preflight.get("errors") or []),
            cook_path=cook_path,
        )

    parent = _node(hou, parent_path)
    ref_paths = {}
    existing_by_ref = {}
    for index, spec in enumerate(nodes):
        ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
        name = str(spec.get("name") or ref)
        path = parent.path().rstrip("/") + "/" + name
        ref_paths[ref] = path
        existing = parent.node(name)
        if existing is not None:
            existing_by_ref[ref] = existing

    seen_targets = set()
    for index, connection in enumerate(connections):
        try:
            input_index = int(connection.get("input", connection.get("input_index", 0)))
        except Exception:
            return _ensure_plan_error(
                preflight,
                [{"code": "INVALID_CONNECTION_INDEX", "index": index}],
                cook_path=cook_path,
            )
        target_path = _ensure_plan_path(
            hou, parent, ref_paths, connection.get("target")
        )
        key = (target_path, input_index)
        if key in seen_targets:
            return _ensure_plan_error(
                preflight,
                [{
                    "code": "DUPLICATE_TARGET_INPUT",
                    "index": index,
                    "target": target_path,
                    "input_index": input_index,
                }],
                cook_path=cook_path,
            )
        seen_targets.add(key)

    summary = _ensure_plan_summary(would_cook=bool(cook_path))
    node_details = []
    connection_details = []
    errors = []

    for index, spec in enumerate(nodes):
        ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
        name = str(spec.get("name") or ref)
        node_type = str(spec.get("type") or spec.get("node_type"))
        path = ref_paths[ref]
        existing = existing_by_ref.get(ref)
        if existing is None:
            summary["create_nodes"] += 1
            node_details.append({
                "ref": ref,
                "path": path,
                "type": node_type,
                "exists": False,
                "action": "CREATE",
                "initial_parameters": _plain_value(dict(spec.get("parms") or {})),
                "initial_state": _plain_value(_normalize_state(dict(spec.get("state") or {}))),
                "parameter_changes": [],
                "state_changes": [],
            })
            continue

        parm_changes = []
        state_changes = []
        try:
            for parm_name, desired in dict(spec.get("parms") or {}).items():
                parm = existing.parm(parm_name)
                if parm is None:
                    raise ValueError(f"Parameter not found: {existing.path()}::{parm_name}")
                before = _plain_value(parm.eval())
                after = _plain_value(desired)
                if not _values_equal(before, after):
                    parm_changes.append({
                        "parameter": parm_name,
                        "before": before,
                        "after": after,
                    })

            for key, desired in _normalize_state(dict(spec.get("state") or {})).items():
                before = _plain_value(_state_value(existing, key))
                after = _plain_value(desired)
                if not _values_equal(before, after):
                    state_changes.append({
                        "state": key,
                        "before": before,
                        "after": after,
                    })
        except Exception as exc:
            errors.append({
                "code": "ENSURE_PLAN_READ_FAILED",
                "path": existing.path(),
                "message": str(exc),
            })
            continue

        action = "UPDATE" if parm_changes or state_changes else "NOOP"
        if action == "UPDATE":
            summary["update_nodes"] += 1
        else:
            summary["noop_nodes"] += 1
        summary["parameter_changes"] += len(parm_changes)
        summary["state_changes"] += len(state_changes)
        node_details.append({
            "ref": ref,
            "path": existing.path(),
            "type": node_type,
            "exists": True,
            "action": action,
            "initial_parameters": {},
            "initial_state": {},
            "parameter_changes": parm_changes,
            "state_changes": state_changes,
        })

    if errors:
        return _ensure_plan_error(preflight, errors, cook_path=cook_path)

    for index, connection in enumerate(connections):
        source_path = _ensure_plan_path(
            hou, parent, ref_paths, connection.get("source")
        )
        target_path = _ensure_plan_path(
            hou, parent, ref_paths, connection.get("target")
        )
        input_index = int(connection.get("input", connection.get("input_index", 0)))
        output_index = int(connection.get("output", connection.get("output_index", 0)))
        target_node = hou.node(target_path)

        if target_node is None:
            before = {"source": None, "output_index": None}
        else:
            try:
                current = node_ops.input_state(hou, target_path, input_index)
            except Exception as exc:
                return _ensure_plan_error(
                    preflight,
                    [{
                        "code": "ENSURE_PLAN_INPUT_READ_FAILED",
                        "index": index,
                        "target": target_path,
                        "input_index": input_index,
                        "message": str(exc),
                    }],
                    cook_path=cook_path,
                )
            before = {
                "source": current.get("source"),
                "output_index": current.get("output_index"),
            }

        after = {
            "source": source_path,
            "output_index": output_index,
        }
        changed = (
            before["source"] != after["source"]
            or before["output_index"] != after["output_index"]
        )
        if changed:
            summary["connection_changes"] += 1
        else:
            summary["noop_connections"] += 1
        connection_details.append({
            "index": index,
            "target": target_path,
            "input_index": input_index,
            "action": "CONNECT" if changed else "NOOP",
            "before": before,
            "after": after,
        })

    cook_target = None
    if cook_path:
        cook_target = _ensure_plan_path(hou, parent, ref_paths, cook_path)
        if cook_target not in ref_paths.values() and hou.node(cook_target) is None:
            return _ensure_plan_error(
                preflight,
                [{"code": "COOK_TARGET_NOT_FOUND", "cook_path": str(cook_path)}],
                cook_path=cook_path,
            )

    summary["noop_total"] = summary["noop_nodes"] + summary["noop_connections"]
    summary["total_changes"] = (
        summary["create_nodes"]
        + summary["parameter_changes"]
        + summary["state_changes"]
        + summary["connection_changes"]
    )

    plan_hash = stable_hash({
        "version": 1,
        "parent": parent.path(),
        "declaration": {
            "nodes": _plain_value(nodes),
            "connections": _plain_value(connections),
            "cook_target": cook_target,
        },
        "observed": {
            "nodes": _plain_value(node_details),
            "connections": _plain_value(connection_details),
        },
    })

    all_details = [
        ("node", item) for item in node_details
    ] + [
        ("connection", item) for item in connection_details
    ]
    returned = all_details[:max_details]
    returned_nodes = [item for kind, item in returned if kind == "node"]
    returned_connections = [item for kind, item in returned if kind == "connection"]

    return {
        "ok": True,
        "verified": True,
        "read_only": True,
        "advisory": True,
        "parent": parent.path(),
        "summary": summary,
        "compact": _ensure_plan_compact(summary),
        "plan_hash": plan_hash,
        "nodes": returned_nodes,
        "connections": returned_connections,
        "cook": {
            "requested": bool(cook_path),
            "target": cook_target,
            "executed": False,
        },
        "detail_total": len(all_details),
        "detail_returned": len(returned),
        "truncated": len(returned) < len(all_details),
        "errors": [],
        "warnings": list(preflight.get("warnings") or []),
        "preflight": preflight,
        "stale_between_plan_and_apply_possible": True,
        "concurrency_note": (
            "Plan is an advisory read-only snapshot, not a lock. "
            "Use expected-hash APIs when a later write must remain conditional "
            "on the state observed by this or any earlier external read."
        ),
    }


def ensure_transactional(
    hou,
    parent_path,
    nodes,
    connections=None,
    *,
    layout=False,
    cook_path=None,
    force=True,
    expected_plan_hash=None,
):
    from . import cook_ops, node_ops

    nodes = list(nodes or [])
    connections = list(connections or [])

    plan_guard = {
        "required": expected_plan_hash is not None,
        "matched": None,
        "expected_plan_hash": expected_plan_hash,
        "actual_plan_hash": None,
    }
    if expected_plan_hash is not None:
        current_plan = plan_ensure(
            hou,
            parent_path,
            nodes,
            connections,
            cook_path=cook_path,
            max_details=0,
        )
        actual_plan_hash = current_plan.get("plan_hash")
        plan_guard["actual_plan_hash"] = actual_plan_hash
        plan_guard["matched"] = bool(
            current_plan.get("ok")
            and actual_plan_hash == expected_plan_hash
        )
        if not plan_guard["matched"]:
            return {
                "conflict": True,
                "verified": False,
                "written": 0,
                "created": [],
                "updated": [],
                "changed_parameters": 0,
                "changed_state": 0,
                "changed_connections": 0,
                "rolled_back": False,
                "plan_guard": plan_guard,
                "current_summary": current_plan.get("summary"),
                "errors": [{
                    "code": "PLAN_CONFLICT",
                    "message": "Current declared graph state no longer matches expected_plan_hash.",
                }],
            }

    preflight = validate_spec(hou, parent_path, nodes, connections)
    if not preflight.get("ok"):
        return {
            "conflict": False,
            "verified": False,
            "written": 0,
            "created": [],
            "updated": [],
            "errors": list(preflight.get("errors") or []),
            "warnings": list(preflight.get("warnings") or []),
            "preflight": preflight,
        }

    parent = _node(hou, parent_path)
    ref_paths = {}
    existing_paths = {}
    for index, spec in enumerate(nodes):
        ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
        name = str(spec.get("name") or ref)
        full_path = parent.path().rstrip("/") + "/" + name
        ref_paths[ref] = full_path
        existing = parent.node(name)
        if existing is not None:
            existing_paths[ref] = existing.path()

    seen_targets = set()
    for index, connection in enumerate(connections):
        target_ref = str(connection.get("target") or "")
        try:
            input_index = int(connection.get("input", connection.get("input_index", 0)))
        except Exception:
            return {
                "conflict": False,
                "verified": False,
                "written": 0,
                "created": [],
                "updated": [],
                "errors": [{"code": "INVALID_CONNECTION_INDEX", "index": index}],
                "preflight": preflight,
            }
        target_path = ref_paths.get(target_ref)
        if target_path is None:
            target_path = target_ref if target_ref.startswith("/") else parent.path().rstrip("/") + "/" + target_ref
        key = (target_path, input_index)
        if key in seen_targets:
            return {
                "conflict": False,
                "verified": False,
                "written": 0,
                "created": [],
                "updated": [],
                "errors": [{
                    "code": "DUPLICATE_TARGET_INPUT",
                    "index": index,
                    "target": target_path,
                    "input_index": input_index,
                }],
                "preflight": preflight,
            }
        seen_targets.add(key)

    node_snapshots = []
    try:
        for index, spec in enumerate(nodes):
            ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
            existing_path = existing_paths.get(ref)
            if existing_path is None:
                continue
            node = _node(hou, existing_path)
            parms = {}
            for name in dict(spec.get("parms") or {}):
                parm = node.parm(name)
                if parm is None:
                    raise ValueError(f"Parameter not found: {node.path()}::{name}")
                parms[name] = parm.eval()
            node_snapshots.append({
                "path": node.path(),
                "parms": parms,
                "state": _state_snapshot(node, dict(spec.get("state") or {})),
            })

        connection_snapshots = []
        snap_keys = set()
        for connection in connections:
            target_ref = str(connection.get("target") or "")
            input_index = int(connection.get("input", connection.get("input_index", 0)))
            target_path = existing_paths.get(target_ref)
            if target_path is None and target_ref.startswith("/") and hou.node(target_ref) is not None:
                target_path = target_ref
            if target_path is None and target_ref not in ref_paths:
                candidate = parent.node(target_ref)
                if candidate is not None:
                    target_path = candidate.path()
            if target_path is None:
                continue
            key = (target_path, input_index)
            if key in snap_keys:
                continue
            snap_keys.add(key)
            connection_snapshots.append(node_ops.input_state(hou, target_path, input_index))
    except Exception as exc:
        return {
            "conflict": False,
            "verified": False,
            "written": 0,
            "created": [],
            "updated": [],
            "errors": [{"code": "ENSURE_SNAPSHOT_FAILED", "message": str(exc)}],
            "preflight": preflight,
        }

    refs = {}
    created = []
    created_nodes = []
    updated = set()
    changed_parameters = 0
    changed_state = 0
    changed_connections = 0
    applied_connections = []
    cook_result = None

    try:
        for index, spec in enumerate(nodes):
            ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
            name = str(spec.get("name") or ref)
            node_type = str(spec.get("type") or spec.get("node_type"))
            node = parent.node(name)
            if node is None:
                node = parent.createNode(
                    node_type,
                    node_name=name,
                    exact_type_name=True,
                    force_valid_node_name=False,
                )
                if node.name() != name or node.type().name() != node_type:
                    raise RuntimeError(f"ENSURE_CREATE_IDENTITY_MISMATCH: {name}")
                created.append(node.path())
                created_nodes.append(node)
                changed_parameters += len(_set_parms(node, dict(spec.get("parms") or {})))
                changed_state += len(_set_state(hou, node, dict(spec.get("state") or {})))
            else:
                node_changed = False
                for parm_name, desired in dict(spec.get("parms") or {}).items():
                    parm = node.parm(parm_name)
                    if parm is None:
                        raise ValueError(f"Parameter not found: {node.path()}::{parm_name}")
                    if not _values_equal(parm.eval(), desired):
                        parm.set(desired)
                        changed_parameters += 1
                        node_changed = True
                for key, desired in _normalize_state(dict(spec.get("state") or {})).items():
                    if not _values_equal(_state_value(node, key), desired):
                        _set_state(hou, node, {key: desired})
                        changed_state += 1
                        node_changed = True
                if node_changed:
                    updated.add(node.path())
            refs[ref] = node

        for connection in connections:
            source = _resolve(hou, parent, refs, str(connection["source"]))
            target = _resolve(hou, parent, refs, str(connection["target"]))
            input_index = int(connection.get("input", connection.get("input_index", 0)))
            output_index = int(connection.get("output", connection.get("output_index", 0)))
            current = node_ops.input_state(hou, target.path(), input_index)
            if current["source"] == source.path() and current["output_index"] == output_index:
                applied_connections.append({
                    "source": source.path(),
                    "target": target.path(),
                    "input": input_index,
                    "output": output_index,
                    "changed": False,
                })
                continue
            target.setInput(input_index, source, output_index)
            after = node_ops.input_state(hou, target.path(), input_index)
            if after["source"] != source.path() or after["output_index"] != output_index:
                raise RuntimeError(f"ENSURE_CONNECTION_READBACK_MISMATCH: {target.path()}[{input_index}]")
            changed_connections += 1
            if target.path() not in created:
                updated.add(target.path())
            applied_connections.append({
                "source": source.path(),
                "target": target.path(),
                "input": input_index,
                "output": output_index,
                "changed": True,
            })

        if layout and hasattr(parent, "layoutChildren"):
            parent.layoutChildren(tuple(refs.values()))

        if cook_path:
            cook_target = _resolve(hou, parent, refs, str(cook_path))
            cook_result = cook_ops.cook(hou, cook_target.path(), force=bool(force))
            if cook_result.get("cook_status") == "FAILED":
                rollback = _restore_ensure_transaction(
                    hou, node_snapshots, connection_snapshots, created
                )
                return {
                    "conflict": False,
                    "verified": False,
                    "written": 0,
                    "created": [],
                    "updated": sorted(updated),
                    "changed_parameters": 0,
                    "changed_state": 0,
                    "changed_connections": 0,
                    "cook": cook_result,
                    "rolled_back": rollback["verified"],
                    "rollback": rollback,
                    "errors": [{"code": "COOK_FAILED", "host_errors": cook_result.get("errors") or []}],
                    "preflight": preflight,
                }

        verification_errors = []
        for index, spec in enumerate(nodes):
            ref = str(spec.get("id") or spec.get("name") or f"node_{index}")
            node = refs[ref]
            node_type = str(spec.get("type") or spec.get("node_type"))
            if node.type().name() != node_type:
                verification_errors.append({"code": "NODE_TYPE_MISMATCH", "path": node.path()})
            for parm_name, desired in dict(spec.get("parms") or {}).items():
                parm = node.parm(parm_name)
                if parm is None or not _values_equal(parm.eval(), desired):
                    verification_errors.append({"code": "PARM_READBACK_MISMATCH", "path": node.path(), "parameter": parm_name})
            for key, desired in _normalize_state(dict(spec.get("state") or {})).items():
                if not _values_equal(_state_value(node, key), desired):
                    verification_errors.append({"code": "STATE_READBACK_MISMATCH", "path": node.path(), "state": key})

        for connection in connections:
            source = _resolve(hou, parent, refs, str(connection["source"]))
            target = _resolve(hou, parent, refs, str(connection["target"]))
            input_index = int(connection.get("input", connection.get("input_index", 0)))
            output_index = int(connection.get("output", connection.get("output_index", 0)))
            after = node_ops.input_state(hou, target.path(), input_index)
            if after["source"] != source.path() or after["output_index"] != output_index:
                verification_errors.append({
                    "code": "CONNECTION_READBACK_MISMATCH",
                    "target": target.path(),
                    "input_index": input_index,
                })

        if verification_errors:
            rollback = _restore_ensure_transaction(
                hou, node_snapshots, connection_snapshots, created
            )
            return {
                "conflict": False,
                "verified": False,
                "written": 0,
                "created": [],
                "updated": sorted(updated),
                "changed_parameters": 0,
                "changed_state": 0,
                "changed_connections": 0,
                "cook": cook_result,
                "rolled_back": rollback["verified"],
                "rollback": rollback,
                "errors": verification_errors,
                "preflight": preflight,
            }

        written = len(created) + changed_parameters + changed_state + changed_connections
        return {
            "conflict": False,
            "verified": True,
            "written": written,
            "plan_guard": plan_guard,
            "parent": parent.path(),
            "created": created,
            "updated": sorted(updated),
            "refs": {key: node.path() for key, node in refs.items()},
            "changed_parameters": changed_parameters,
            "changed_state": changed_state,
            "changed_connections": changed_connections,
            "connections": applied_connections,
            "cook": cook_result,
            "rolled_back": False,
            "preflight": preflight,
        }
    except Exception as exc:
        rollback = _restore_ensure_transaction(
            hou, node_snapshots, connection_snapshots, created
        )
        return {
            "conflict": False,
            "verified": False,
            "written": 0,
            "created": [],
            "updated": sorted(updated),
            "changed_parameters": 0,
            "changed_state": 0,
            "changed_connections": 0,
            "cook": cook_result,
            "rolled_back": rollback["verified"],
            "rollback": rollback,
            "errors": [{
                "code": "ENSURE_FAILED",
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }],
            "preflight": preflight,
        }
