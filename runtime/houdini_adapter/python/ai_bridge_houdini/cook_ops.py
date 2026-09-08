def set_frame(hou, frame):
    before = hou.frame()
    hou.setFrame(float(frame))
    after = hou.frame()
    return {"before": before, "after": after, "verified": float(after) == float(frame)}


def cook(hou, path, force=True):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    node.cook(force=bool(force))
    errors = list(node.errors())
    warnings = list(node.warnings())
    messages = list(node.messages()) if hasattr(node, "messages") else []
    return {
        "path": path,
        "errors": errors,
        "warnings": warnings,
        "messages": messages,
        "cook_status": "PASS" if not errors else "FAILED",
    }


def host_errors(hou, path):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return {"path": path, "errors": list(node.errors()), "warnings": list(node.warnings())}
