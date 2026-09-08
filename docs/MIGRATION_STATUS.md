# AI Bridge Public Repository Migration Status

**Date:** 2026-09-09

## Objective

Move distributable AI Bridge product authority out of the developer Bus repository into `fmb22333-dev/AI-Bridge` without interrupting the developer's active Houdini projects or live Bridge Runtime.

## Required product properties

| Requirement | Current migration state |
|---|---|
| GitHub binding | **IN PROGRESS** — clean Bus provisioning implementation is migrated and retargeted to this repository |
| Independent Knowledge Pack | **IN PROGRESS** — clean distribution filter migrated; public knowledge boundary documented |
| First-run creates/initializes GitHub Bus | **IN PROGRESS** — clean Bus templates and provisioner migrated; full Setup UI/Runtime wiring still pending |
| AI understands protocol automatically | **FOUNDATION COMPLETE** — AI Agent Protocol + generated Bus machine entrypoint/read-first/root-README hints are present |
| Auditable updates | **FOUNDATION COMPLETE** — canonical `CHANGELOG.md`, change policy, remote preflight/recheck workflow, and AI maintenance rules are present |
| Shared product update source | **CONTRACT COMPLETE / INTEGRATION PENDING** — Supervisor `0.1.3` resolver defaults to `fmb22333-dev/AI-Bridge:main` and forbids implicit Runtime publication into the user Bus |

## Migrated now

### Protocol/specification/audit

- `AGENTS.md`
- `CHANGELOG.md`
- `specs/AI_AGENT_PROTOCOL.md`
- `specs/AI_BRIDGE_EXECUTION_SPEC.md`
- `specs/AI_BRIDGE_LEARNING_POLICY.md`
- `specs/AI_BRIDGE_DEPLOYMENT_SPEC.md`
- `docs/FIRST_INSTALL_FLOW.md`
- `docs/REPOSITORY_MODEL.md`
- `docs/UPDATE_WORKFLOW.md`
- `docs/CHANGE_POLICY.md`

### Generated Bus templates

- `templates/bus/PROJECT_STATE_INDEX.template.json`
- `templates/bus/AI_BRIDGE_READ_FIRST.template.md`
- `templates/bus/README.template.md`

Generated indices point to `fmb22333-dev/AI-Bridge:main` for shared Runtime/protocol authority. They do not require the old developer repository's `bridge-runtime` branch.

### Runtime/deployment source

Migrated:

- package metadata/root/config
- GitHub Bus provisioner
- clean Knowledge Pack generator
- host plugin manifest
- protocol package (`command`, `capability`, `result`, `errors`)
- Core foundation:
  - `adapter_bus.py`
  - `conflicts.py`
  - `emergency_stop.py`
  - `policy.py`
  - `remote_adapter.py`
  - `sessions.py`
  - `workspace.py`
- Persistence foundation:
  - `db.py`
  - `checkpoints.py`
  - `evidence.py`
  - `history.py`
  - `recovery.py`
  - `snapshots.py`
- Security foundation:
  - Windows DPAPI secret store
  - non-Windows fail-closed fallback
- Transport foundation:
  - `base.py`
  - `remote_config.py`
  - `local_api.py`
  - transport package entrypoint
- tests:
  - clean GitHub provisioning
  - clean Knowledge distribution
  - canonical Supervisor update-source regression

Still pending before Runtime can be considered runnable from this repository:

- `core/service.py`
- `core/execution_policy.py`
- `core/host_process.py`
- `core/plugin_manager.py`
- remaining adapters / transport GitHub bus / result delivery / runner
- app/web/Setup UI
- Runtime launch/install scripts
- complete test suite

### Bootstrap / Supervisor

Present:

- legacy bootstrap entry slice under `bootstrap/supervisor/0.1.2/`
- new `bootstrap/supervisor/0.1.3/supervisor_update_source.py`

The legacy Supervisor used the user's Bus as a fallback Runtime release source. That behavior is now explicitly forbidden. The `0.1.3` resolver implements the corrected product contract, but full Supervisor source integration is still pending.

## Explicitly excluded from the product repository

- `.ai-bridge/commands/`
- `.ai-bridge/results/`
- developer live status/presence
- developer Bridge ID
- AutoUV/Retarget/SubdivNormalBake/Locomotion project authority/history
- local HIP/FBX paths
- command/recovery history from the developer machine
- credentials/secrets
- project-family candidate knowledge as generic execution authority
- generated package/cache metadata (`*.egg-info`, `__pycache__`, `.pytest_cache`, etc.)

## Live-development isolation

The developer Bus remains `fmb22333-dev/ai-bridge-bus` and remains authority for the developer's live Bridge/Houdini state until an explicit migration/activation is validated.

At this checkpoint the live Runtime remains `0.2.6.37` with the existing Houdini sessions untouched. This migration has not sent Host mutation commands and has not restarted Houdini, replaced the active Adapter, switched Runtime, modified a HIP, or rewritten the developer Bus configuration.

## Mandatory audit workflow

`read CHANGELOG -> remote preflight -> reconcile -> modify -> validate -> update CHANGELOG -> remote recheck -> synchronize`

During the current initialization window the user has declared this repository single-writer, but the workflow remains mandatory so multi-user collaboration can be enabled later without changing process.

## Next migration sequence

1. Integrate the `0.1.3` shared-product update-source resolver into the complete Supervisor and migrate remaining bootstrap files.
2. Complete Runtime Core by whitelist (`execution_policy`, process/plugin management, then `service`).
3. Complete Transport and app/web/Setup UI.
4. Migrate Houdini Adapter kernel.
5. Generate and commit only the clean distributable Knowledge Pack output.
6. Migrate installer assembly and release-manifest logic.
7. Migrate/retarget Blender and Unreal adapter skeletons.
8. Run repository regression tests.
9. Build a clean installer from this repository.
10. Validate on a fresh Windows environment / fresh Bus repository.
11. Only after acceptance, move live developer Runtime update authority away from the old developer repository.

## Acceptance boundary

The repository is **not yet a complete release**. The architecture, audit model, Bus provisioning, Knowledge filtering, Supervisor update-source contract, and several Runtime foundation layers are now present. Full Runtime integration and fresh-install validation remain pending.
