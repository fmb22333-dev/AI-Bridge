from __future__ import annotations

_RUNTIME = None


def start():
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    import hou
    if not hou.isUIAvailable():
        raise RuntimeError("AI Bridge V1 Houdini Adapter currently requires Houdini UI mode")
    from .client import HoudiniBridgeRuntime
    _RUNTIME = HoudiniBridgeRuntime(hou)
    hou.ui.addEventLoopCallback(_RUNTIME.tick)
    _RUNTIME.start()
    return _RUNTIME


def stop():
    global _RUNTIME
    if _RUNTIME is None:
        return
    import hou
    try:
        hou.ui.removeEventLoopCallback(_RUNTIME.tick)
    except Exception:
        pass
    _RUNTIME.stop()
    _RUNTIME = None
