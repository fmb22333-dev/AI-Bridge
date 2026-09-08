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
- Runtime persistence slice: SQLite command/result store, checkpoint/evidence/history/recovery/snapshot stores.
- Runtime security slice: Windows DPAPI secret-store implementation plus non-Windows fail-closed fallback.
- Runtime transport slice started: transport contract, GitHub remote configuration, and authenticated local Adapter API.
- Supervisor `0.1.3` update-source resolver with regression coverage.

### Changed
- Product/runtime/protocol authority is being migrated from `ai-bridge-bus:bridge-runtime` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared protocol/runtime authority.
- AI maintenance workflow now requires changelog review/update in addition to remote preflight and post-write recheck.
- Supervisor update-source semantics are now explicit: default product updates resolve to `fmb22333-dev/AI-Bridge:main`; a user Bus is never an implicit Runtime release source.
- Duplicate Supervisor update-source tests were consolidated into one canonical regression contract.

### Safety
- Public/distributable content explicitly excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family candidate knowledge.
- Repository migration is isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` release branch inside the user's Bus is forbidden by the new product contract.
- Runtime credentials remain local-only and encrypted with Windows DPAPI; no credential material is committed to the product repository.

### Pending
- Integrate the `0.1.3` shared-product update-source resolver into the full Supervisor implementation and migrate remaining bootstrap files.
- Complete remaining Runtime Core (`service.py`, execution policy, plugin/process management).
- Complete remaining persistence/transport helpers, then migrate app/web/Setup UI.
- Complete Houdini Adapter migration.
- Generate and commit clean Knowledge Pack output.
- Migrate installer assembly, release manifest, Blender/Unreal adapter skeletons.
- Run full repository regression and fresh-Windows install validation.

## 2026-09-09 — Repository bootstrap

- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
