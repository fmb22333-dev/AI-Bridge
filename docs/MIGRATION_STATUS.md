# AI Bridge Public Repository Migration Status

**Date:** 2026-09-09

## Objective

Move distributable AI Bridge product authority out of the developer Bus repository into `fmb22333-dev/AI-Bridge` without interrupting active Houdini projects or the live Bridge Runtime, while allowing the development source and Knowledge to continue evolving.

## Current baseline

Machine-readable authority: `migration/BASELINE.json`.

- source: `fmb22333-dev/ai-bridge-bus:bridge-runtime`
- source baseline commit: `03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`
- Runtime source tree: `7ff7c780ba910bf96220b5d7c80e5f830f71cc2e`
- live Runtime observed: `0.2.6.40`
- Houdini Adapter: `0.5.21`
- Knowledge tree: `a1a2e26e15c29e67860e375b1fdc194e27763ea6`
- promotion registry: `8993f527074169a106326079fe48f911a869c9f6`
- recipes tree: `6db974be24446efc7ea06f798ea50355dca92bb0`

Source synchronization follows `docs/DELTA_SYNC_WORKFLOW.md`. The baseline advances only after reconciliation, validation, synchronization, and changelog audit.

## Product requirements

| Requirement | State |
|---|---|
| GitHub binding | **IN PROGRESS / CORE PRESENT** — GitHub Bus transport and clean provisioning source migrated |
| Independent Knowledge Pack | **IN PROGRESS** — deterministic clean filter present; final output must be regenerated from latest source before release |
| First-run initializes independent GitHub Bus | **IN PROGRESS** — templates/provisioner/Setup frontend present; full rebased Setup routes pending |
| AI understands protocol automatically | **FOUNDATION COMPLETE** — Bus README/read-first/index and product Agent protocol are established |
| Auditable updates | **FOUNDATION COMPLETE** — CHANGELOG, change policy, baseline, Delta policy, remote pre/post checks |
| Shared product update source | **CONTRACT COMPLETE / SUPERVISOR INTEGRATION PENDING** — product source is `fmb22333-dev/AI-Bridge:main`, never the user Bus |
| Runtime Core | **MAJOR FOUNDATION MIGRATED** — Core service, execution/recovery, persistence/security and V5 transport are present |
| Houdini Adapter | **MAJOR FOUNDATION MIGRATED** — dispatcher/primitives/graph transaction layer present; client/knowledge registry remain |

## Migrated product foundation

### Protocol / audit / deployment

- `AGENTS.md`
- `CHANGELOG.md`
- `migration/BASELINE.json`
- `docs/DELTA_SYNC_WORKFLOW.md`
- execution / learning / deployment / AI-agent specs
- Bus templates and README bootstrap hint
- clean GitHub provisioner
- clean Knowledge Pack generator/filter

### Runtime

Present now:

- protocol models
- Runtime config/package entrypoints
- Core service/control plane
- execution budget policy
- Adapter command bus / session / workspace registries
- host process control and plugin manager
- checkpoint/evidence/snapshot/recovery/SQLite persistence
- Windows DPAPI secret store
- local Adapter API
- remote configuration/controller
- result delivery / runner
- GitHub transport with Contents fallback, V2/V3/V4 compatibility and V5 multi-channel
- Runtime launcher and Houdini Adapter installer

### Houdini Adapter

Present now:

- package/bootstrap/version
- dispatcher
- inspect/context/batch inspection
- parameter/code primitives with expected-hash guards
- node operations and atomic batch connections
- Cook/checkpoint/error classification
- capability guidance
- graph validate/apply/transactional/ensure-plan/ensure-transactional
- local log/package manifest

Remaining major Houdini files:

- `client.py`
- `knowledge_registry.py`
- remaining support modules/data
- generated clean distributable Knowledge output

### Web / Setup

Present now:

- Web package entrypoint
- Setup frontend
- command-history page and script
- Dashboard template
- Setup frontend defaults shared Runtime source to `fmb22333-dev/AI-Bridge`

Pending:

- full `web/routes.py` REBASE so product update config is `AI-Bridge:main` + `runtime-release.json`
- remaining Dashboard static assets
- product Setup contract is intentionally RED until routes are integrated

### Supervisor

Present:

- legacy 0.1.2 reference slice
- 0.1.3 product update-source resolver
- regression proving user Bus is never implicit Runtime source

The mature 0.1.2 process monitoring, validation, activation and rollback logic remains reusable. Integration must replace the two incompatible legacy paths:

1. `_load_update_config()` Bus fallback;
2. `_bootstrap_release_if_needed()` publication of Runtime into a user's Bus.

Default product update authority is `fmb22333-dev/AI-Bridge:main` with `runtime-release.json`.

## Delta classification

- **SAFE COPY:** ordinary generic Runtime/Adapter fixes and generic tests where product architecture has not diverged.
- **REBASE REQUIRED:** Supervisor/update source, Setup/provisioning, installer/release authority, Bus templates, or any productized file.
- **DO NOT DISTRIBUTE:** `.ai-bridge` runtime state/history, credentials, Bridge IDs, local sessions/workspaces/paths, project authority/handoffs, caches, or candidate/project-specific knowledge as generic execution authority.

Knowledge changes are detected independently from Runtime/Adapter versions by Knowledge-tree and promotion hashes.

## Live isolation

The developer Bus remains live authority for the developer machine. This repository migration has not:

- sent Host mutation commands;
- restarted Houdini;
- replaced the active Adapter;
- switched the live Runtime;
- modified a HIP;
- rewritten the developer Bus configuration.

The live Runtime observed while establishing this baseline was `0.2.6.40`; this movement from earlier `0.2.6.37` migration observations is the reason continuous Baseline + Delta synchronization is now mandatory.

## Next executable sequence

1. Rebase full Setup `web/routes.py` to product update authority and finish Web static assets.
2. Integrate Supervisor 0.1.3 resolver into the mature Supervisor body and remove Bus bootstrap publication.
3. Migrate `bridge_admin.py`.
4. Migrate Houdini `client.py` and `knowledge_registry.py` plus required generic knowledge source.
5. Migrate remaining generic tests and run `compileall + pytest`.
6. Re-read source HEAD and perform first baseline-to-current Delta classification/reconciliation.
7. Regenerate clean Knowledge Pack from the latest reconciled Knowledge tree.
8. Add product `runtime-release.json`, installer assembly and clean release flow.
9. Build clean installer and validate fresh Windows + fresh GitHub Bus installation.
10. Only after acceptance, change live developer update authority away from the old development source.

## Acceptance boundary

The product repository is **not yet a release**. It now has a durable synchronization model and most of the Runtime control/transport foundation, but Setup backend, Supervisor integration, remaining Houdini kernel/Knowledge, full tests, release manifest and fresh-install validation are still required.
