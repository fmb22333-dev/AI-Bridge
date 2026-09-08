from .hashing import stable_hash


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
    value = parm.evalAsString()
    return {"path": path, "parameter": parameter, "text": value, "hash": stable_hash(value)}


def write(hou, path, parameter, text, expected_hash):
    before = read(hou, path, parameter)
    if expected_hash is None:
        raise ValueError("EXPECTED_HASH_REQUIRED")
    if before["hash"] != expected_hash:
        return {"conflict": True, "before": before}
    _, parm = _parm(hou, path, parameter)
    parm.set(text)
    after = read(hou, path, parameter)
    return {"conflict": False, "before": before, "after": after, "verified": after["text"] == text}


def patch(hou, path, parameter, old_text, new_text, expected_hash):
    before = read(hou, path, parameter)
    if expected_hash is None:
        raise ValueError("EXPECTED_HASH_REQUIRED")
    if before["hash"] != expected_hash:
        return {"conflict": True, "before": before}
    count = before["text"].count(old_text)
    if count != 1:
        raise ValueError(f"PATCH_MATCH_COUNT_INVALID: {count}")
    requested = before["text"].replace(old_text, new_text, 1)
    _, parm = _parm(hou, path, parameter)
    parm.set(requested)
    after = read(hou, path, parameter)
    return {"conflict": False, "before": before, "after": after, "verified": after["text"] == requested}
