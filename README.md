# AI Bridge

> **AI / Agent:** In a user Bus, read `PROJECT_STATE_INDEX.json` first. In this product repository, read `AGENTS.md` before modifying source.

AI Bridge is a distributable local Runtime for AI-assisted control of host applications. The shared product repository and each installation's Bus repository have deliberately different authority.

## Authority model

- **This repository** — Runtime, Supervisor, host adapters, installer, protocol/specifications, clean generic Knowledge and release artifacts.
- **Per-install GitHub Bus** — `PROJECT_STATE_INDEX.json`, durable Presence, project authority and GitHub V5 fallback transport.
- **Supabase** — primary realtime command/result transport when Presence reports it connected.
- **BridgeDB** — local command identity/idempotency authority keyed by canonical `command_id`.

A transport failover never creates a second execution identity. Preserve the same `command_id`.

## Normal command path

```text
GitHub PROJECT_STATE_INDEX / Presence
              |
              v
      discover current routing
              |
      +-------+--------+
      |                |
      v                v
Supabase PRIMARY   GitHub V5 FALLBACK
      \                /
       \              /
        v            v
          Bridge Runtime
               |
          Host Adapter
               |
        Houdini / Unreal
```

Blender remains scaffold-only.

## First install

`download -> INSTALL_AI_BRIDGE.bat -> Setup UI -> connect/create GitHub Bus -> configure Supabase Primary -> install host plugins`

The installer must not import developer machine state, Bridge IDs, credentials, workspaces, sessions, command history or project documents.

See [docs/FIRST_INSTALL_FLOW.md](docs/FIRST_INSTALL_FLOW.md).

## AI integration

An authorized AI entering a generated Bus should:

1. read `PROJECT_STATE_INDEX.json`;
2. read the indexed durable Presence;
3. use `transport.ping` when current reachability matters;
4. follow `command_transport_policy` / `supabase_transport`;
5. read project authority only for the selected project;
6. read normative product specs from this repository.

Do not recover live state from chat memory or static release documents.

Normative integration contract: [specs/AI_AGENT_PROTOCOL.md](specs/AI_AGENT_PROTOCOL.md).

## Repository contents

This repository may contain Runtime/Core source, Supervisor bootstrap, Houdini/Unreal adapter payloads, installer/release tooling, normative specs, clean generic Knowledge and deterministic release artifacts.

It must not contain developer credentials, machine Bridge IDs, live workspaces/sessions/PIDs, command/recovery history, local paths, active project authority, project-specific HIP/FBX data or private project-family execution evidence.

## Current product status

- Runtime **0.2.6.88**
- Supervisor **0.1.6**
- Houdini Adapter **0.5.27**
- AIBridgeUE **0.5.3**
- Blender Adapter **scaffold**

The Runtime bundle includes a pinned Windows wheelhouse for offline first-launch dependency bootstrap. Host Plugins supports one-click Houdini install/update and project-scoped Unreal installation.

## Canonical docs

- [specs/AI_BRIDGE_EXECUTION_SPEC.md](specs/AI_BRIDGE_EXECUTION_SPEC.md)
- [specs/AI_AGENT_PROTOCOL.md](specs/AI_AGENT_PROTOCOL.md)
- [specs/AI_BRIDGE_DEPLOYMENT_SPEC.md](specs/AI_BRIDGE_DEPLOYMENT_SPEC.md)
- [specs/AI_BRIDGE_LEARNING_POLICY.md](specs/AI_BRIDGE_LEARNING_POLICY.md)
- [docs/SUPABASE_PRIMARY_TRANSPORT.md](docs/SUPABASE_PRIMARY_TRANSPORT.md)
- [docs/UPDATE_WORKFLOW.md](docs/UPDATE_WORKFLOW.md)
- [docs/CHANGE_POLICY.md](docs/CHANGE_POLICY.md)
- [migration/BASELINE.json](migration/BASELINE.json)

Product history belongs in `CHANGELOG.md` and Git history; current live/project state belongs in the user's Bus.
