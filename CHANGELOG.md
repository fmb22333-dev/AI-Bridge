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
- Machine-readable continuous migration baseline at `migration/BASELINE.json` plus `docs/DELTA_SYNC_WORKFLOW.md`.
- Runtime foundation: protocol, Core service/recovery control plane, persistence, security, execution policy, host/plugin process control, local/remote transport, result delivery, GitHub V2/V3/V4 compatibility and V5 multi-channel transport.
- Houdini Adapter foundation: bootstrap, dispatcher, inspection, parameter/code/node primitives, batch/transaction guards, Cook/checkpoint/error handling, capability guidance, graph transaction/ensure engine, and package manifest.
- Runtime launch and Houdini Adapter installation scripts.
- Setup UI and command-history Web package migration started.
- Supervisor `0.1.3` update-source resolver with regression coverage.

### Changed
- Product/runtime/protocol authority is being migrated from `ai-bridge-bus:bridge-runtime` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared protocol/runtime authority.
- AI maintenance workflow now requires changelog review/update, migration-baseline reconciliation when applicable, and remote preflight/post-write recheck.
- Supervisor update-source semantics are explicit: default product updates resolve to `fmb22333-dev/AI-Bridge:main`; a user Bus is never an implicit Runtime release source.
- Setup UI product-source fallback now points to `fmb22333-dev/AI-Bridge`, while the repository created for each user remains that user's independent Bus.
- Continuous source synchronization now classifies changes as SAFE COPY, REBASE REQUIRED, or DO NOT DISTRIBUTE.
- Knowledge synchronization is defined as deterministic regeneration from the latest source Knowledge tree, not incremental copying of an older clean output.

### Migration baseline
- Development source baseline: `fmb22333-dev/ai-bridge-bus:bridge-runtime@03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`.
- Source Runtime tree: `7ff7c780ba910bf96220b5d7c80e5f830f71cc2e`.
- Live Runtime observed at capture: `0.2.6.40`.
- Houdini Adapter: `0.5.21`.
- Knowledge tree: `a1a2e26e15c29e67860e375b1fdc194e27763ea6`; promotion registry blob `8993f527074169a106326079fe48f911a869c9f6`; recipes tree `6db974be24446efc7ea06f798ea50355dca92bb0`.
- Baseline is advanced only after a source delta is reconciled, validated, synchronized, and audited here.

### Safety
- Public/distributable content explicitly excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family/candidate knowledge as generic execution authority.
- Repository migration is isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` release branch inside the user's Bus is forbidden by the new product contract.
- Runtime credentials remain local-only and encrypted with Windows DPAPI; no credential material is committed to the product repository.
- Productized files are not blindly overwritten during Delta Sync; architecture-sensitive source changes require explicit rebase/reconciliation.

### Validation status
- Product Setup update-source contract has been added as a RED regression while the full rebased `web/routes.py` is still pending. This is intentional and must not be reported as passing yet.
- Full `compileall + pytest` remains pending until the Runtime/Web/Adapter dependency graph is complete.

### Pending
- Rebase and migrate full `web/routes.py` to `AI-Bridge:main` + `runtime-release.json` product-update authority; finish remaining Dashboard assets.
- Integrate the `0.1.3` shared-product update-source resolver into the full Supervisor implementation and migrate remaining bootstrap files.
- Migrate `bridge_admin.py`, Houdini `client.py`, `knowledge_registry.py`, and remaining Adapter support files.
- Generate the clean Knowledge Pack from the latest source Knowledge tree.
- Migrate installer assembly/release manifest and Blender/Unreal adapter scaffolds.
- Perform first Baseline -> current-source Delta Sync, then advance baseline only after validation.
- Run full repository regression, build a clean installer, and validate a fresh-Windows / fresh-Bus installation.

## 2026-09-09 — Repository bootstrap

- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
