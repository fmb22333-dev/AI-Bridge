# AI Bridge

> **AI / Agent:** Before changing this repository, read `AGENTS.md`. Before changing a generated user Bus, read that Bus repository's `PROJECT_STATE_INDEX.json` first. Do not start with repository-wide discovery when the index already provides authoritative pointers.

AI Bridge is a distributable local bridge/runtime for AI-assisted control of host applications such as Houdini, Unreal Engine, and Blender.

## Product requirements

AI Bridge is organized around these requirements:

1. **GitHub primary binding** — each installation connects to GitHub through the user's own authorization.
2. **Independent Knowledge Pack** — generic validated knowledge is distributed independently from private project history.
3. **Automatic Bus initialization** — first-run setup creates or connects a clean per-install GitHub Bus repository automatically.
4. **AI self-onboarding** — an authorized AI reads `PROJECT_STATE_INDEX.json` first and can discover the protocol/state without relying on prior chat memory.
5. **Transport redundancy** — GitHub V5 remains primary, while an optional Supabase Data API transport can run in parallel as an independent emergency ingress/result path.

## Repository role

This repository is the shared **Runtime / Adapter / Installer / Generic Knowledge / Protocol** source.

It is intentionally separate from each user's per-install **Bus repository**.

A user installation creates its own Bus repository containing runtime presence, transport resources, project authority documents, and the machine entrypoint `PROJECT_STATE_INDEX.json`.

## Changelog / audit

Meaningful product changes are recorded in [`CHANGELOG.md`](CHANGELOG.md). Maintainers and AI agents should read it before material Bridge/Knowledge work.

Logging rules are defined in [`docs/CHANGE_POLICY.md`](docs/CHANGE_POLICY.md).

## Target first-run flow

`download -> install -> Setup UI -> authorize GitHub -> Create & Connect Bus -> install host adapters -> ready`

The user should not manually create Bridge JSON files, issues/comments, status folders, Bridge IDs, project index records, or AI onboarding instructions.

Every provisioned Bus receives a root README that tells AI agents to read `PROJECT_STATE_INDEX.json` before searching the repository.

See [`docs/FIRST_INSTALL_FLOW.md`](docs/FIRST_INSTALL_FLOW.md).

## Transport model

GitHub V5 Issue Comment + Contents remains the canonical primary transport.

Runtime **0.2.6.54** adds an optional **Supabase fallback transport**. It is deliberately polled in parallel instead of activating only after GitHub failure. This solves the case where an AI client loses GitHub write capability while Bridge and GitHub remain otherwise healthy.

Both transports carry the same canonical `CommandEnvelope` and converge on the same local Bridge command database. `command_id` remains the single execution identity, so the same command observed through both transports reuses durable state/result instead of authorizing a second host-side mutation.

See [`docs/SUPABASE_FALLBACK_TRANSPORT.md`](docs/SUPABASE_FALLBACK_TRANSPORT.md).

## AI integration

AI agents working on this product repository should read `AGENTS.md` first.

AI agents entering a generated user Bus repository must read that Bus repository's `PROJECT_STATE_INDEX.json` before any project or Bridge operation.

The public integration contract is [`specs/AI_AGENT_PROTOCOL.md`](specs/AI_AGENT_PROTOCOL.md).

## Update and collaboration rule

Bridge Runtime, Adapter, Installer, protocol, and promoted generic Knowledge Pack changes must remain synchronized with this repository.

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
- Generic transport adapters and deployment schema

It must not contain:

- developer-machine Bridge IDs
- GitHub tokens, Supabase secret keys, or other secret material
- current workspaces/sessions/PIDs
- command or recovery history
- local install paths
- active project authority documents
- project-specific HIP/FBX paths
- private project-family execution recipes

## Current product status

Runtime **0.2.6.54**, Houdini Adapter **0.5.26**, Supervisor **0.1.4**.

GitHub remains the default transport. Supabase fallback support is shipped but remains inert until a project/table and local secret credential are configured. The fallback credential is stored through the existing local SecretStore/Windows DPAPI path and is never committed.

The release workflow rebuilds and verifies deterministic Runtime/installer artifacts on `main`.

See [`docs/SUPABASE_FALLBACK_TRANSPORT.md`](docs/SUPABASE_FALLBACK_TRANSPORT.md) and [`docs/MIGRATION_STATUS.md`](docs/MIGRATION_STATUS.md).

The existing developer Runtime/Bus is intentionally not switched merely by repository migration or release assembly.
