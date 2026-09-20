try:
    from ai_bridge_houdini.bootstrap import start
    start()
except Exception as exc:
    print("[AI Bridge] Houdini adapter not started:", exc)
