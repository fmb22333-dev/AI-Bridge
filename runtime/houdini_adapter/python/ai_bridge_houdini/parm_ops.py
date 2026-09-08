from .hashing import stable_hash
from .inspect_ops import _safe


def _parm(hou, path, parameter):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    parm = node.parm(parameter)
    if parm is None:
        raise ValueError(f"PARM_NOT_FOUND: {path}/{parameter}")
    return node, parm


def read(hou, path, parameter):
    _, parm = _parm(hou, path, parameter)
    value = _safe(parm.eval())
    return {"path": path, "parameter": parameter, "value": value, "hash": stable_hash(value)}


def write(hou, path, parameter, value, expected_hash):
    before = read(hou, path, parameter)
    if expected_hash is None:
        raise ValueError("EXPECTED_HASH_REQUIRED")
    if before["hash"] != expected_hash:
        return {"conflict": True, "before": before}
    _, parm = _parm(hou, path, parameter)
    parm.set(value)
    after = read(hou, path, parameter)
    return {
        "conflict": False,
        "before": before,
        "after": after,
        "verified": after["value"] == _safe(value),
    }
