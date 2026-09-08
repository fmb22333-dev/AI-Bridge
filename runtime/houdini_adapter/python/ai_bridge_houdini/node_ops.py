from .hashing import stable_hash


def _node(hou, path):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return node


def create(hou, parent_path, node_type, name):
    parent = _node(hou, parent_path)
    existing = parent.node(name) if hasattr(parent, "node") else hou.node(parent_path.rstrip("/") + "/" + name)
    if existing is not None:
        return {"conflict": True, "reason": "NODE_NAME_EXISTS", "path": existing.path()}
    node = parent.createNode(
        node_type,
        name,
        exact_type_name=True,
        force_valid_node_name=False,
    )
    exact_type = node.type().name()
    verified = node.name() == name and exact_type == node_type
    return {
        "conflict": False,
        "path": node.path(),
        "type": exact_type,
        "name": node.name(),
        "verified": verified,
    }


def _input_output_index(target, input_index):
    input_index = int(input_index)
    try:
        for connection in target.inputConnections():
            if int(connection.inputIndex()) == input_index:
                return int(connection.outputIndex())
    except Exception:
        pass
    return None


def input_state(hou, target_path, input_index):
    target = _node(hou, target_path)
    input_index = int(input_index)
    source = target.input(input_index)
    state = {
        "target": target_path,
        "input_index": input_index,
        "source": None if source is None else source.path(),
        "output_index": None if source is None else _input_output_index(target, input_index),
    }
    state["hash"] = stable_hash(state)
    return state


def connect(hou, target_path, input_index, source_path, output_index, expected_hash):
    before = input_state(hou, target_path, input_index)
    if expected_hash is None:
        raise ValueError("EXPECTED_HASH_REQUIRED")
    if before["hash"] != expected_hash:
        return {"conflict": True, "before": before}
    target = _node(hou, target_path)
    source = _node(hou, source_path)
    output_index = int(output_index)
    target.setInput(int(input_index), source, output_index)
    after = input_state(hou, target_path, input_index)
    return {
        "conflict": False,
        "before": before,
        "after": after,
        "verified": after["source"] == source_path and after["output_index"] == output_index,
    }


def disconnect(hou, target_path, input_index, expected_hash):
    before = input_state(hou, target_path, input_index)
    if expected_hash is None:
        raise ValueError("EXPECTED_HASH_REQUIRED")
    if before["hash"] != expected_hash:
        return {"conflict": True, "before": before}
    target = _node(hou, target_path)
    target.setInput(int(input_index), None)
    after = input_state(hou, target_path, input_index)
    return {
        "conflict": False,
        "before": before,
        "after": after,
        "verified": after["source"] is None and after["output_index"] is None,
    }


def normalize_batch_connect_args(args):
    args = args or {}
    if "items" in args:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")
        return items, "items"

    if "target" not in args and "connections" not in args:
        raise ValueError("ARGUMENT_REQUIRED: items or target+connections")

    target = str(args.get("target") or "").strip()
    connections = args.get("connections")
    if not target:
        raise ValueError("ARGUMENT_INVALID: target must be a non-empty string")
    if not isinstance(connections, list) or not connections:
        raise ValueError("ARGUMENT_INVALID: connections must be a non-empty list")

    items = []
    for index, connection in enumerate(connections):
        if not isinstance(connection, dict):
            raise ValueError(f"ARGUMENT_INVALID: connections[{index}] must be an object")
        item = dict(connection)
        item["target"] = target
        items.append(item)
    return items, "target+connections"


def _normalized_connection_item(item, index):
    if not isinstance(item, dict):
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] must be an object")
    target = str(item.get("target") or "").strip()
    source = str(item.get("source") or "").strip()
    if not target or not source:
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] requires target and source")
    try:
        input_index = int(item.get("input_index", item.get("input", 0)))
        output_index = int(item.get("output_index", item.get("output", 0)))
    except Exception as exc:
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] has invalid connection index") from exc
    if input_index < 0 or output_index < 0:
        raise ValueError(f"ARGUMENT_INVALID: items[{index}] connection indices must be non-negative")
    expected_hash = item.get("expected_hash")
    if expected_hash is None:
        raise ValueError(f"EXPECTED_HASH_REQUIRED: items[{index}]")
    return {
        "target": target,
        "input_index": input_index,
        "source": source,
        "output_index": output_index,
        "expected_hash": expected_hash,
    }


def _restore_states(hou, snapshots):
    errors = []
    for snapshot in reversed(snapshots):
        try:
            target = _node(hou, snapshot["target"])
            if snapshot["source"] is None:
                target.setInput(snapshot["input_index"], None)
            else:
                source = _node(hou, snapshot["source"])
                output_index = snapshot.get("output_index")
                target.setInput(
                    snapshot["input_index"],
                    source,
                    0 if output_index is None else int(output_index),
                )
        except Exception as exc:
            errors.append({
                "target": snapshot.get("target"),
                "input_index": snapshot.get("input_index"),
                "message": str(exc),
            })

    verified = not errors
    if verified:
        for snapshot in snapshots:
            try:
                current = input_state(hou, snapshot["target"], snapshot["input_index"])
                if (
                    current["source"] != snapshot["source"]
                    or current["output_index"] != snapshot["output_index"]
                ):
                    verified = False
                    errors.append({
                        "target": snapshot["target"],
                        "input_index": snapshot["input_index"],
                        "message": "rollback readback mismatch",
                    })
            except Exception as exc:
                verified = False
                errors.append({
                    "target": snapshot.get("target"),
                    "input_index": snapshot.get("input_index"),
                    "message": str(exc),
                })
    return {"verified": verified, "errors": errors}


def batch_connect(hou, items):
    if not isinstance(items, list) or not items:
        raise ValueError("ARGUMENT_INVALID: items must be a non-empty list")

    normalized = []
    seen = set()
    for index, item in enumerate(items):
        value = _normalized_connection_item(item, index)
        key = (value["target"], value["input_index"])
        if key in seen:
            raise ValueError(
                f"ARGUMENT_INVALID: duplicate target input {value['target']}[{value['input_index']}]"
            )
        seen.add(key)
        _node(hou, value["target"])
        _node(hou, value["source"])
        normalized.append(value)

    preflight = []
    conflicts = []
    for item in normalized:
        before = input_state(hou, item["target"], item["input_index"])
        preflight.append(before)
        if before["hash"] != item["expected_hash"]:
            conflicts.append({
                "target": item["target"],
                "input_index": item["input_index"],
                "expected_hash": item["expected_hash"],
                "actual_hash": before["hash"],
                "before": before,
            })

    if conflicts:
        return {
            "conflict": True,
            "late_conflict": False,
            "written": 0,
            "verified": False,
            "rolled_back": False,
            "conflicts": conflicts,
            "preflight": preflight,
            "items": [],
        }

    touched = []
    results = []
    try:
        for item, before in zip(normalized, preflight):
            current = input_state(hou, item["target"], item["input_index"])
            if current["hash"] != item["expected_hash"]:
                rollback = _restore_states(hou, touched)
                return {
                    "conflict": True,
                    "late_conflict": True,
                    "written": 0,
                    "verified": False,
                    "rolled_back": rollback["verified"],
                    "rollback": rollback,
                    "conflicts": [{
                        "target": item["target"],
                        "input_index": item["input_index"],
                        "expected_hash": item["expected_hash"],
                        "actual_hash": current["hash"],
                        "before": current,
                    }],
                    "preflight": preflight,
                    "items": [],
                }

            touched.append(before)
            target = _node(hou, item["target"])
            source = _node(hou, item["source"])
            target.setInput(item["input_index"], source, item["output_index"])
            after = input_state(hou, item["target"], item["input_index"])
            if after["source"] != item["source"] or after["output_index"] != item["output_index"]:
                raise RuntimeError(
                    f"BATCH_CONNECT_READBACK_MISMATCH: {item['target']}[{item['input_index']}]"
                )
            results.append({"before": before, "after": after, "verified": True})
    except Exception as exc:
        rollback = _restore_states(hou, touched)
        return {
            "conflict": False,
            "late_conflict": False,
            "written": 0,
            "verified": False,
            "rolled_back": rollback["verified"],
            "rollback": rollback,
            "error": {
                "code": "BATCH_CONNECT_FAILED",
                "message": str(exc),
                "exception_type": type(exc).__name__,
            },
            "preflight": preflight,
            "items": [],
        }

    return {
        "conflict": False,
        "late_conflict": False,
        "written": len(results),
        "verified": True,
        "rolled_back": False,
        "preflight": preflight,
        "items": results,
    }


def delete(hou, path, expected_type, expected_name):
    node = _node(hou, path)
    actual_type = node.type().name()
    actual_name = node.name()
    if actual_type != expected_type or actual_name != expected_name:
        return {
            "conflict": True,
            "reason": "NODE_IDENTITY_CHANGED",
            "actual_type": actual_type,
            "actual_name": actual_name,
        }
    node.destroy()
    return {"conflict": False, "verified": hou.node(path) is None, "path": path}
