# Unreal Adapter

AI Bridge ships **AIBridgeUE 0.5.3** as a project-scoped Unreal Engine editor plugin.

Current product status:

- Host Plugins reports Unreal as `ready`.
- The installer copies the bundled plugin into the selected project's `Plugins/AIBridgeUE`.
- Stale plugin-local `Binaries/` and `Intermediate/` are removed during install/update.
- Installation fails closed when the target Unreal Editor is running.
- Bridge does not automatically close or restart Unreal Editor.

The adapter contains the native editor/session integration plus bounded project tooling/resources currently distributed with AIBridgeUE.
