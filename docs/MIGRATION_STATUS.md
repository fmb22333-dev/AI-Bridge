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
| AI understands protocol automatically | **FOUNDATION COMPLETE** — AI Agent Protocol + generated Bus machine entrypoint/read-first templates are present |

## Migrated now

### Protocol/specification layer

- `AGENTS.md`
- `specs/AI_AGENT_PROTOCOL.md`
- `specs/AI_BRIDGE_EXECUTION_SPEC.md`
- `specs/AI_BRIDGE_LEARNING_POLICY.md`
- `specs/AI_BRIDGE_DEPLOYMENT_SPEC.md`
- `docs/FIRST_INSTALL_FLOW.md`
- `docs/REPOSITORY_MODEL.md`

### Generated Bus templates

- `templates/bus/PROJECT_STATE_INDEX.template.json`
- `templates/bus/AI_BRIDGE_READ_FIRST.template.md`

Generated indices point to:

- product repository: `fmb22333-dev/AI-Bridge`
- product ref: `main`
- public specs under `specs/`

They no longer require the old developer repository's `bridge-runtime` branch.

### Runtime/deployment source started

- `runtime/pyproject.toml`
- `runtime/src/ai_bridge/deployment/github_provision.py`
- `runtime/src/ai_bridge/deployment/knowledge_pack.py`
- `runtime/tests/test_clean_github_provision.py`
- `runtime/tests/test_clean_distribution_knowledge.py`

The public GitHub provisioner now generates Bus indices pointing to this product repository and includes the public AI protocol pointer.

### Bootstrap started

- `bootstrap/supervisor/0.1.2/AI_Bridge.bat`

The remainder of Supervisor/bootstrap is not yet marked public-ready because its Runtime update-source assumptions must be reviewed/retargeted before copying.

## Explicitly not migrated

The following developer/runtime evidence must not be copied into the public product repository:

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

## Live-development isolation

The current developer Bus remains `fmb22333-dev/ai-bridge-bus` and remains authority for the developer's live Bridge/Houdini state until an explicit migration/activation is validated.

This repository migration must not:

- restart Houdini;
- replace the currently installed Houdini Adapter;
- switch the active developer Runtime;
- modify a current HIP;
- rewrite the developer Bus configuration.

## Next migration sequence

1. Migrate/retarget remaining Supervisor bootstrap source.
2. Migrate Runtime Core source by whitelist (exclude generated egg-info/cache/state).
3. Migrate Houdini Adapter kernel.
4. Generate the clean Knowledge Pack from current development knowledge and copy **only the generated clean output**.
5. Migrate Setup UI and connect it to the retargeted GitHub Bus provisioner.
6. Migrate installer assembly source and release manifest logic.
7. Migrate/retarget Blender and Unreal adapter skeletons.
8. Run public-repository regression tests.
9. Build a clean installer from this repository.
10. Validate on a fresh Windows environment / fresh Bus repository.
11. Only after acceptance, move Runtime update authority away from the old developer repository.

## Acceptance boundary

The repository is **not yet a complete public release**. The protocol and deployment foundation are established and source migration has begun. The old live Runtime remains untouched by design.
