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

## Changelog / audit

Meaningful product changes are recorded in [`CHANGELOG.md`](CHANGELOG.md). Maintainers and AI agents should read it before material Bridge/Knowledge work so they can see what changed, what remains pending, and whether an installation/release may need updating.

Logging rules are defined in [`docs/CHANGE_POLICY.md`](docs/CHANGE_POLICY.md).

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

Before modifying those areas, an AI or human maintainer must read `CHANGELOG.md`, inspect current head/recent commits and relevant PRs, reconcile newer remote work, then modify from that state. After validation, update the changelog when required and check remote state again before publication.

Required sequence:

`read changelog -> remote preflight -> reconcile -> modify -> validate -> update changelog -> remote recheck -> synchronize/publish`

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

## Current product status

Runtime **0.2.6.50**, Houdini Adapter **0.5.26**, Supervisor **0.1.4**, clean Knowledge, release manifests, deterministic Runtime/installer bundles, Setup backend, and the Windows installer are assembled and product-validated.

Windows product validation passes `compileall`, **84/84** tests, release verification, PowerShell parsing, artifact publication, and a clean GitHub re-download/install smoke test.

The only remaining gate is explicit real-environment acceptance: provision a genuinely new user Bus through Setup UI, authorize/connect it, activate the Houdini Adapter, and verify a live Houdini round-trip.

See [`docs/MIGRATION_STATUS.md`](docs/MIGRATION_STATUS.md).

The existing developer Runtime/Bus is intentionally not switched by repository migration or release assembly.
