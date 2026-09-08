# AI Bridge Public Repository Migration Status

**Date:** 2026-09-09

## Objective

Move distributable AI Bridge product authority out of the developer Bus repository into `fmb22333-dev/AI-Bridge` without interrupting the developer's active Houdini projects or live Bridge Runtime.

## Four required product properties

| Requirement | Current migration state |
|---|---|
| GitHub binding | **IN PROGRESS** — public Bus provisioning implementation migrated and retargeted to this repository |
| Independent Knowledge Pack | **IN PROGRESS** — clean distribution filter migrated; public knowledge boundary documented |
| First-run creates/initializes GitHub Bus | **IN PROGRESS** — clean Bus templates and provisioner migrated; full Setup UI/Runtime wiring still to migrate |
| AI understands protocol automatically | **FOUNDATION COMPLETE** — AI Agent Protocol + generated Bus machine entrypoint/read-first/root-README hints are present |
| Auditable updates | **FOUNDATION COMPLETE** — canonical `CHANGELOG.md`, change policy, remote preflight/recheck workflow, and AI maintenance rule are present |

## Migrated now

### Protocol/specification and audit layer

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

Generated indices point to:

- product repository: `fmb22333-dev/AI-Bridge`
- product ref: `main`
- public specs under `specs/`

They no longer require the old developer repository's `bridge-runtime` branch.

### Runtime/deployment source

Migrated:

- `runtime/pyproject.toml`
- `runtime/src/ai_bridge/__init__.py`
- `runtime/src/ai_bridge/config.py`
- `runtime/src/ai_bridge/deployment/github_provision.py`
- `runtime/src/ai_bridge/deployment/knowledge_pack.py`
- `runtime/src/ai_bridge/host_plugins.json`
- Runtime protocol package (`protocol/__init__.py`, `command.py`, `capability.py`, `result.py`, `errors.py`)
- initial Runtime Core package:
  - `core/__init__.py`
  - `core/adapter_bus.py`
  - `core/conflicts.py`
  - `core/emergency_stop.py`
  - `core/policy.py`
  - `core/remote_adapter.py`
  - `core/sessions.py`
  - `core/workspace.py`
- `runtime/tests/test_clean_github_provision.py`
- `runtime/tests/test_clean_distribution_knowledge.py`
- `runtime/tests/test_public_supervisor_update_source.py`

Still pending in Runtime Core:

- `core/service.py`
- `core/execution_policy.py`
- `core/host_process.py`
- `core/plugin_manager.py`
- adapters / persistence / security / transport / web / app layers
- Runtime launch/install scripts and complete test suite

### Bootstrap / Supervisor

Migrated so far:

- `bootstrap/supervisor/0.1.2/AI_Bridge.bat`

Important blocker identified:

The legacy Supervisor fallback uses the user's configured Bus repository and creates a `bridge-runtime` branch there when no explicit update source exists. That behavior conflicts with the new two-repository product model. A regression contract now forbids blind migration of that behavior. The Supervisor must be retargeted to shared-product update authority before its full source is accepted here.

## Explicitly not migrated

The following developer/runtime evidence must not be copied into the product repository:

- `.ai-bridge/commands/`
- `.ai-bridge/results/`
- developer live status/presence
- developer Bridge ID
- AutoUV project authority/history
- Retarget project authority/history
- SubdivNormalBake/Locomotion project authority/history
- local HIP/FBX paths
- command/recovery history
- credentials/secrets
- project-family candidate knowledge as generic execution authority
- generated Python package metadata/cache (`*.egg-info`, `__pycache__`, `.pytest_cache`, etc.)

## Live-development isolation

The current developer Bus remains `fmb22333-dev/ai-bridge-bus` and remains authority for the developer's live Bridge/Houdini state until an explicit migration/activation is validated.

Live status at this migration checkpoint reports Runtime `0.2.6.37` and two connected Houdini sessions. This repository migration has not sent host mutation commands and must not:

- restart Houdini;
- replace the currently installed Houdini Adapter;
- switch the active developer Runtime;
- modify a current HIP;
- rewrite the developer Bus configuration.

## Mandatory collaboration/audit workflow

Before material Bridge/Knowledge changes:

`read CHANGELOG -> remote preflight (commits + PRs) -> reconcile -> modify -> validate -> update CHANGELOG -> remote recheck -> synchronize`

Meaningful product changes are not considered synchronized if their required changelog entry is missing.

## Next migration sequence

1. Retarget and migrate remaining Supervisor/bootstrap source so product updates come from shared `AI-Bridge`, not the user's Bus.
2. Complete remaining Runtime Core source by whitelist.
3. Migrate persistence/security/transport/app/web layers and Setup UI.
4. Migrate Houdini Adapter kernel.
5. Generate the clean Knowledge Pack from current development knowledge and copy **only the generated clean output**.
6. Migrate installer assembly source and release manifest logic.
7. Migrate/retarget Blender and Unreal adapter skeletons.
8. Run public-repository regression tests.
9. Build a clean installer from this repository.
10. Validate on a fresh Windows environment / fresh Bus repository.
11. Only after acceptance, move live developer Runtime update authority away from the old developer repository.

## Acceptance boundary

The repository is **not yet a complete release**. Protocol, audit, Bus provisioning, Knowledge filtering, and a first Runtime source slice are now present. The old live Runtime remains untouched by design.
