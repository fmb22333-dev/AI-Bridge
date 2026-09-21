# First Install Flow

This document describes the current end-user installation path.

## User flow

1. Download the latest public AI Bridge installer.
2. Run `INSTALL_AI_BRIDGE.bat`.
3. The installer places the current Runtime and Supervisor under the local AI Bridge install directory.
4. Start AI Bridge; Setup opens locally.
5. Authorize GitHub and create or connect a dedicated per-install Bus.
6. Configure **Supabase Primary Bus** if realtime transport is required.
7. Install the desired Host plugins:
   - Houdini — supported.
   - Unreal Engine — project-scoped AIBridgeUE installation supported.
   - Blender — scaffold only.

Users should not manually create Bridge JSON, status folders, transport comments, Bridge IDs or project-index records.

## Local state created on the target machine

- unique Bridge ID;
- local config/data directory;
- credential storage;
- Runtime connection state;
- Workspace registry;
- Host-plugin installation state.

No developer-machine state is copied into a clean installation.

## GitHub Bus created/connected by Setup

A clean Bus contains the machine entrypoint and durable authority needed by AI clients, including:

- `PROJECT_STATE_INDEX.json`;
- `AI_BRIDGE_READ_FIRST.md`;
- `.ai-bridge/status/<bridge_id>.json`;
- GitHub fallback transport resources;
- project authority documents only after projects are explicitly registered.

The generated index points to this shared product repository for Runtime/protocol/spec authority.

## Transport roles

GitHub is durable authority and GitHub V5 fallback transport. Supabase is the primary realtime command/result path when configured and connected. Both converge on the same local BridgeDB command identity.

## Security

GitHub and Supabase credentials remain in local secret storage and are never committed to either repository. A clean installation starts with `projects={}`.

## Current validated package

- Runtime **0.2.6.89**
- Supervisor **0.1.6**
- Houdini Adapter **0.5.27**
- AIBridgeUE **0.5.3**
- bundled Windows wheelhouse for offline first-launch dependency bootstrap

Automated Windows validation covers clean GitHub re-download/install and offline Python dependency bootstrap. Interactive fresh-account Bus authorization plus real Houdini/Unreal host acceptance remains a separate end-user acceptance gate.
