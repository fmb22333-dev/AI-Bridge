# AI Bridge

> **AI / Agent:** Before changing this repository, read `AGENTS.md`. Before changing a generated user Bus, read that Bus repository's `PROJECT_STATE_INDEX.json` first. Do not start with repository-wide discovery when the index already provides authoritative pointers.

AI Bridge is a distributable local bridge/runtime for AI-assisted control of host applications such as Houdini, Unreal Engine, and Blender.

## Product requirements

AI Bridge is organized around these requirements:

1. **GitHub authority binding** — each installation connects to GitHub through the user's own authorization for durable Presence, project authority, Runtime/release metadata, documentation, Knowledge and audit/recovery.
2. **Supabase primary realtime transport** — normal interactive command/result traffic uses Supabase when configured and connected.
3. **GitHub V5 fallback command transport** — GitHub remains a fully supported command path when Supabase is unavailable.
4. **Independent Knowledge Pack** — generic validated knowledge is distributed independently from private project history.
5. **Automatic Bus initialization** — first-run setup creates or connects a clean per-install GitHub Bus repository automatically.
6. **AI self-onboarding** — an authorized AI reads `PROJECT_STATE_INDEX.json` and Presence first, then follows `command_transport_policy` instead of relying on chat memory.
7. **Cross-transport idempotency** — failover preserves the same canonical `command_id`; BridgeDB remains the execution authority.

## Repository role

This repository is the shared **Runtime / Adapter / Installer / Generic Knowledge / Protocol** source.

It is intentionally separate from each user's per-install **Bus repository**.

A user installation creates its own Bus repository containing runtime Presence, transport discovery resources, project authority documents, and the machine entrypoint `PROJECT_STATE_INDEX.json`.

## Changelog / audit

Meaningful product changes are recorded in [`CHANGELOG.md`](CHANGELOG.md) and release-specific documents under `docs/`.

Logging rules are defined in [`docs/CHANGE_POLICY.md`](docs/CHANGE_POLICY.md).

## Target first-run flow

`download -> install -> Setup UI -> authorize GitHub authority/fallback Bus -> configure Supabase Primary Bus -> install host adapters -> ready`

The user should not manually create Bridge JSON files, issues/comments, status folders, Bridge IDs, project index records, or AI onboarding instructions.

Every provisioned Bus receives a root README that tells AI agents to read `PROJECT_STATE_INDEX.json` and Presence before searching the repository or sending commands.

See [`docs/FIRST_INSTALL_FLOW.md`](docs/FIRST_INSTALL_FLOW.md).

## Transport model

Runtime **0.2.6.56+** uses this role split:

```text
GitHub Presence / PROJECT_STATE_INDEX  -> discovery + durable authority
                    |
                    v
Supabase PRIMARY_FAST                 -> normal command/result traffic
                    |
          unavailable / degraded
                    v
GitHub V5 FALLBACK                    -> fallback command/result traffic
```

Both transports carry the same canonical `CommandEnvelope` and converge on the same local Bridge command database. `command_id` remains the single execution identity, so transport failover never authorizes a second Host mutation merely because a different bus was used.

Supabase uses one row per command, which allows independent AI conversations to submit without sharing a comment/mailbox. The Runtime uses bounded execution lanes: same Host Session is FIFO/serialized; different Host Sessions or unrelated Runtime adapter lanes may progress concurrently; result publication remains serialized at the transport boundary.

Runtime 0.2.6.x retains historical fallback-named configuration/API surfaces for compatibility. New integrations should discover roles from `supabase_transport` and `command_transport_policy`, not from legacy names.

See [`docs/SUPABASE_PRIMARY_TRANSPORT.md`](docs/SUPABASE_PRIMARY_TRANSPORT.md). The older [`docs/SUPABASE_FALLBACK_TRANSPORT.md`](docs/SUPABASE_FALLBACK_TRANSPORT.md) is retained as implementation history and compatibility documentation.

## AI integration

AI agents working on this product repository should read `AGENTS.md` first.

AI agents entering a generated user Bus repository must read that Bus repository's `PROJECT_STATE_INDEX.json` and current Presence before any project or Bridge operation.

Normal routing is:

1. discover through GitHub authority;
2. use Supabase for commands when Presence reports it connected and primary;
3. use GitHub V5 only as fallback or for explicit GitHub transport testing;
4. preserve the same `command_id` across failover.

The public integration contract is [`specs/AI_AGENT_PROTOCOL.md`](specs/AI_AGENT_PROTOCOL.md).

## Update and collaboration rule

Bridge Runtime, Adapter, Installer, protocol, and promoted generic Knowledge Pack changes must remain synchronized with this repository.

Required sequence:

`read changelog -> remote preflight -> reconcile -> modify -> validate -> update docs/changelog -> remote recheck -> synchronize/publish`

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

Runtime **0.2.6.56**, Houdini Adapter **0.5.26**, Supervisor **0.1.4**.

Supabase is the default realtime command/result transport when configured. GitHub V5 remains connected as fallback command transport and remains the durable authority. Supabase credentials are stored through the existing local SecretStore/Windows DPAPI path and are never committed.

The release workflow rebuilds and verifies deterministic Runtime/installer artifacts on `main`, including a fresh-install smoke test.

See [`docs/SUPABASE_PRIMARY_TRANSPORT.md`](docs/SUPABASE_PRIMARY_TRANSPORT.md), [`docs/RELEASE_0.2.6.56.md`](docs/RELEASE_0.2.6.56.md), and [`docs/MIGRATION_STATUS.md`](docs/MIGRATION_STATUS.md).
