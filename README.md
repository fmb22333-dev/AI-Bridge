# AI Bridge

> **AI / Agent:** Before changing this repository, read `AGENTS.md`. Before changing a generated user Bus, read that Bus repository's `PROJECT_STATE_INDEX.json` first. Do not start with repository-wide discovery when the index already provides authoritative pointers.

AI Bridge is a distributable local bridge/runtime for AI-assisted control of host applications such as Houdini, Unreal Engine, and Blender.

## The four product requirements

AI Bridge is organized around four non-negotiable requirements:

1. **GitHub binding** — each installation connects to GitHub through the user's own authorization.
2. **Independent Knowledge Pack** — generic validated knowledge is distributed independently from private project history.
3. **Automatic Bus initialization** — first-run setup creates or connects a clean per-install GitHub Bus repository automatically.
4. **AI self-onboarding** — an authorized AI reads `PROJECT_STATE_INDEX.json` first and can discover the protocol/state without relying on prior chat memory.

## Repository role

This repository is the shared **Runtime / Adapter / Installer / Generic Knowledge / Protocol** source.

It is intentionally separate from each user's per-install **Bus repository**.

A user installation creates its own Bus repository containing runtime presence, transport resources, project authority documents, and the machine entrypoint `PROJECT_STATE_INDEX.json`.

## Target first-run flow

`download -> install -> Setup UI -> authorize GitHub -> Create & Connect Bus -> install host adapters -> ready`

The user should not manually create Bridge JSON files, issues/comments, status folders, Bridge IDs, project index records, or AI onboarding instructions.

Every provisioned Bus receives a root README that tells AI agents to read `PROJECT_STATE_INDEX.json` before searching the repository.

See [`docs/FIRST_INSTALL_FLOW.md`](docs/FIRST_INSTALL_FLOW.md).

## AI integration

AI agents working on this product repository should read `AGENTS.md` first.

AI agents entering a generated user Bus repository must read that Bus repository's `PROJECT_STATE_INDEX.json` before any project or Bridge operation.

The public integration contract is [`specs/AI_AGENT_PROTOCOL.md`](specs/AI_AGENT_PROTOCOL.md).

## Update and collaboration rule

Bridge Runtime, Adapter, Installer, protocol, and promoted generic Knowledge Pack changes must remain synchronized with this repository.

Before modifying those areas, an AI or human maintainer should inspect the current repository head/recent commits and relevant PRs, reconcile newer remote work, then modify from that state. After validation, remote state should be checked again before publication.

Required sequence:

`remote preflight -> reconcile -> modify -> validate -> remote recheck -> synchronize/publish`

See [`docs/UPDATE_WORKFLOW.md`](docs/UPDATE_WORKFLOW.md).

## Distribution boundary

This repository may contain:

- Bridge Core and Supervisor
- Host adapters
- Installer/bootstrap assets
- Protocol and normative specifications
- Clean generic Knowledge Pack
- Clean Bus/project templates
- Release manifests and distributable bundles

It must not contain:

- developer-machine Bridge IDs
- GitHub tokens or secret material
- current workspaces/sessions/PIDs
- command or recovery history
- local install paths
- active project authority documents
- project-specific HIP/FBX paths
- private project-family execution recipes

## Current migration status

The independent product repository is initialized and the public protocol layer is now present:

- AI Agent Protocol
- Execution Specification
- Learning Policy
- Deployment Specification
- clean Bus `PROJECT_STATE_INDEX` template
- generated `AI_BRIDGE_READ_FIRST` template
- generated Bus root README AI bootstrap
- Knowledge Pack boundary
- first-install flow
- repository synchronization/update workflow
- Supervisor/bootstrap migration has started

The old development Bus remains the live developer/runtime authority until Runtime/installer source migration and clean-distribution validation are completed. This migration does not activate or replace the developer's running Runtime or Houdini Adapter.
