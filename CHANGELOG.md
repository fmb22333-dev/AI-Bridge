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
- Migration status and first-install architecture documentation.

### Changed
- Product/runtime/protocol authority is being migrated from `ai-bridge-bus:bridge-runtime` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared protocol/runtime authority.

### Safety
- Public/distributable content explicitly excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family candidate knowledge.
- Repository migration is isolated from the developer's active Houdini sessions and live Runtime.

### Pending
- Complete Supervisor/bootstrap migration and retarget update-source assumptions.
- Complete Runtime Core migration.
- Complete Houdini Adapter migration.
- Generate and commit clean Knowledge Pack output.
- Migrate Setup UI, installer assembly, Blender/Unreal adapter skeletons.
- Run full repository regression and fresh-Windows install validation.

## 2026-09-09 — Repository bootstrap

- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
