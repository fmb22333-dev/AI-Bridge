from __future__ import annotations

from .hashing import stable_hash
from .inspect_ops import _safe


def _node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return node


def _parm(node, name: str):
    parm = node.parm(name)
    if parm is None:
        raise ValueError(f"PARM_NOT_FOUND: {node.path()}/{name}")
    return parm


def _read_parm(parm):
    value = _safe(parm.eval())
    return {"value": value, "hash": stable_hash(value)}


def _safe_set(parm, value):
    parm.set(value)


def ensure(
    hou,
    path: str,
    count_parameter: str,
    minimum_count,
    values: dict | None = None,
    expected_count_hash: str | None = None,
) -> dict:
    node = _node(hou, str(path))
    count_parameter = str(count_parameter or "").strip()
    if not count_parameter:
        raise ValueError("ARGUMENT_REQUIRED: count_parameter")
    count_parm = _parm(node, count_parameter)

    try:
        minimum_count = int(minimum_count)
    except Exception:
        raise ValueError("ARGUMENT_INVALID: minimum_count must be an integer")
    if minimum_count < 0 or minimum_count > 10000:
        raise ValueError("ARGUMENT_INVALID: minimum_count must be in 0..10000")

    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("ARGUMENT_INVALID: values must be an object")
    if len(values) > 256:
        raise ValueError("ARGUMENT_INVALID: values exceeds maximum 256")
    normalized_values = {}
    for raw_name, value in values.items():
        name = str(raw_name or "").strip()
        if not name:
            raise ValueError("ARGUMENT_INVALID: values contains an empty parameter name")
        normalized_values[name] = value

    count_before = _read_parm(count_parm)
    if expected_count_hash is not None and count_before["hash"] != expected_count_hash:
        return {
            "conflict": True,
            "path": node.path(),
            "count_parameter": count_parameter,
            "expected_count_hash": expected_count_hash,
            "actual_count_hash": count_before["hash"],
            "before": count_before,
            "written": 0,
        }

    try:
        current_count = int(count_before["value"])
    except Exception:
        raise ValueError("MULTIPARM_COUNT_NOT_INTEGER")

    target_count = max(current_count, minimum_count)
    snapshots = {}
    for name in normalized_values:
        parm = node.parm(name)
        if parm is None:
            snapshots[name] = {"exists": False}
        else:
            state = _read_parm(parm)
            snapshots[name] = {"exists": True, **state}

    count_changed = target_count != current_count
    touched = []

    def rollback():
        errors = []
        # Restore values that existed before resize while their instances are
        # still present, then restore the original multiparm count.
        for name in reversed(touched):
            snapshot = snapshots.get(name) or {}
            if not snapshot.get("exists"):
                continue
            try:
                parm = node.parm(name)
                if parm is not None:
                    _safe_set(parm, snapshot.get("value"))
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        if count_changed:
            try:
                _safe_set(count_parm, current_count)
            except Exception as exc:
                errors.append(f"{count_parameter}: {type(exc).__name__}: {exc}")
        return errors

    try:
        if count_changed:
            _safe_set(count_parm, target_count)
            count_after_resize = _read_parm(count_parm)
            if int(count_after_resize["value"]) < minimum_count:
                raise RuntimeError("MULTIPARM_RESIZE_READBACK_MISMATCH")

        resolved = {}
        for name in normalized_values:
            parm = node.parm(name)
            if parm is None:
                raise RuntimeError(f"MULTIPARM_CHILD_NOT_RESOLVED: {name}")
            resolved[name] = parm

        for name, value in normalized_values.items():
            parm = resolved[name]
            before_value = _safe(parm.eval())
            if before_value != _safe(value):
                _safe_set(parm, value)
                touched.append(name)

        count_after = _read_parm(count_parm)
        children = {}
        verified = int(count_after["value"]) >= minimum_count
        for name, value in normalized_values.items():
            parm = node.parm(name)
            if parm is None:
                verified = False
                children[name] = {"missing": True}
                continue
            actual = _safe(parm.eval())
            match = actual == _safe(value)
            verified = verified and match
            children[name] = {
                "value": actual,
                "hash": stable_hash(actual),
                "verified": match,
            }

        if not verified:
            raise RuntimeError("MULTIPARM_READBACK_MISMATCH")

        return {
            "conflict": False,
            "verified": True,
            "path": node.path(),
            "count_parameter": count_parameter,
            "minimum_count": minimum_count,
            "before": {
                "count": current_count,
                "count_hash": count_before["hash"],
                "children": snapshots,
            },
            "after": {
                "count": int(count_after["value"]),
                "count_hash": count_after["hash"],
                "children": children,
            },
            "count_changed": count_changed,
            "written": len(touched) + (1 if count_changed else 0),
            "child_writes": len(touched),
            "rolled_back": False,
        }
    except Exception as exc:
        rollback_errors = rollback()
        restored = _read_parm(count_parm)
        rollback_verified = int(restored["value"]) == current_count and not rollback_errors
        return {
            "conflict": False,
            "verified": False,
            "path": node.path(),
            "count_parameter": count_parameter,
            "minimum_count": minimum_count,
            "before": {
                "count": current_count,
                "count_hash": count_before["hash"],
                "children": snapshots,
            },
            "target_count": target_count,
            "rolled_back": True,
            "rollback_verified": rollback_verified,
            "rollback_errors": rollback_errors,
            "failure": {
                "code": "MULTIPARM_ENSURE_FAILED",
                "category": "transaction",
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": False,
                "suggestion": "Inspect the multiparm count parameter and generated child names before retrying.",
            },
        }
