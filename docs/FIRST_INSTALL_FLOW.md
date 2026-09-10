# First Install Flow

This is the target end-user flow for AI Bridge.

## What the user does

1. Download the latest AI Bridge installer/release package.
2. Run `INSTALL_AI_BRIDGE` / the packaged installer.
3. The Setup UI opens locally.
4. Authorize GitHub for a dedicated AI Bridge Bus repository.
5. Click **Create & Connect Bus**.
6. Select which host adapters to install (Houdini / Unreal / Blender).
7. Finish setup.

The user should not manually create GitHub JSON files, status folders, issues, comments, Bridge IDs, or project records.

## What Bridge does automatically

### A. Local initialization

Generate local-only state:

- Bridge ID
- local data/config directory
- credential/secret storage
- Runtime connection config
- host adapter installation state
- Workspace registry

None of this machine-specific state is copied from the maintainer/developer installation.

### B. GitHub Bus provisioning

Create or connect a clean dedicated user repository, then initialize:

- `PROJECT_STATE_INDEX.json`
- `AI_BRIDGE_READ_FIRST.md`
- `.ai-bridge/status/<bridge_id>.json`
- required GitHub transport resources

The generated index points back to the shared product repository `fmb22333-dev/AI-Bridge` for normative protocol/specification/runtime source.

### C. AI onboarding

A user can then give an AI access to the Bus repository.

The only mandatory onboarding rule is:

> Read `PROJECT_STATE_INDEX.json` first.

From there the AI can discover:

- the live Runtime status;
- available host sessions/workspaces;
- registered projects;
- project authority documents;
- the shared Runtime/product repository;
- AI/Bridge protocol and execution/learning/deployment specifications.

The AI must not rely on previous chat memory as state authority.

## Repository split

```text
fmb22333-dev/AI-Bridge
  shared product/runtime
  installer
  adapters
  clean generic knowledge
  protocol/specs
          |
          | first-run setup
          v
user/ai-bridge-bus
  PROJECT_STATE_INDEX.json
  AI_BRIDGE_READ_FIRST.md
  .ai-bridge/status/...
  transport resources
  user project authority docs
          |
          v
local AI Bridge
          |
          v
Houdini / Unreal / Blender
```

## Security boundary

GitHub access is always performed through the user's authorized GitHub identity/app/token. Credentials stay in local secret storage and are never committed to the Bus or public product repository.

A third-party AI can only modify what its authorized GitHub identity is allowed to modify.

## Project creation

A clean installation starts with `projects={}`.

Project authority documents and project entries are created only when the user explicitly registers/starts a project. Installing AI Bridge must never import the maintainer's AutoUV, Retarget, local HIP paths, command history, or other project state.

## Implementation / validation status — 2026-09-10

The installer and release assembly described above are implemented for the current Houdini product slice:

- Runtime 0.2.6.50
- Houdini Adapter 0.5.26
- Supervisor 0.1.4
- `AI_Bridge_Installer.zip` containing the one-click BAT/PowerShell entrypoints
- deterministic Runtime/release manifests with SHA-256 verification

Automated Windows validation has passed a clean GitHub re-download/install smoke test.

**Still pending:** the interactive end-user acceptance path that actually creates a brand-new GitHub Bus through Setup UI, authorizes/connects it, activates Houdini Adapter in that fresh installation, and proves a live Houdini round-trip. This is deliberately kept separate from the automated installer smoke test.
