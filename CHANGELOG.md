# AI Bridge Changelog

This is the human-readable product change log for `fmb22333-dev/AI-Bridge`.

It records meaningful changes to Runtime, Supervisor, host adapters, installer/setup, protocol, distributable Knowledge Pack, safety behavior, migration state, and release authority.

For logging rules, see `docs/CHANGE_POLICY.md`.

## Unreleased

### Added
- Independent `AI-Bridge` product repository separated from the developer Bus.
- Canonical AI onboarding contract: generated Bus repositories point agents to `PROJECT_STATE_INDEX.json` first.
- Generated Bus root README bootstrap hint to prevent unnecessary repository-wide discovery.
- Clean GitHub Bus provisioning source retargeted to `fmb22333-dev/AI-Bridge`.
- Clean distributable Knowledge Pack filtering source and regression-test skeleton.
- Mandatory remote preflight/recheck workflow for multi-human / multi-AI updates.
- Canonical `CHANGELOG.md` plus `docs/CHANGE_POLICY.md` for product audit and update decisions.
- Initial Runtime source migration: protocol models, package/config helpers, workspace registry, session registry, adapter command bus, remote adapter executor, deterministic policy, conflict hashing, emergency write stop, and host-plugin manifest.
- Public Supervisor contract test that forbids default Runtime publication into a user's Bus repository.

### Changed
- Product/runtime/protocol authority is being migrated from `ai-bridge-bus:bridge-runtime` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared protocol/runtime authority.
- AI maintenance workflow now requires changelog review/update in addition to remote preflight and post-write recheck.

### Safety
- Public/distributable content explicitly excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family candidate knowledge.
- Repository migration is isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` release branch inside the user's Bus has been identified as incompatible with the new two-repository model and is blocked from blind migration pending retargeting.

### Pending
- Finish Supervisor/bootstrap migration with shared-product update-source semantics.
- Complete remaining Runtime Core (`service.py`, execution policy, plugin/process management, persistence/security/transport/web/app layers).
- Complete Houdini Adapter migration.
- Generate and commit clean Knowledge Pack output.
- Migrate Setup UI, installer assembly, Blender/Unreal adapter skeletons.
- Run full repository regression and fresh-Windows install validation.

## 2026-09-09 — Repository bootstrap

- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
