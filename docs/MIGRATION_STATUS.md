# AI Bridge Public Repository Migration Status

**Date:** 2026-09-10

## Objective

Move distributable AI Bridge product authority out of the developer Bus repository into `fmb22333-dev/AI-Bridge` without interrupting the developer's live Bridge/Houdini work, while allowing the development source and Knowledge to continue evolving.

## Authority and baseline

Machine-readable migration authority remains `migration/BASELINE.json`.

Current baseline is intentionally **not advanced**:

- source: `fmb22333-dev/ai-bridge-bus:bridge-runtime`
- baseline commit: `03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`
- Runtime source tree: `7ff7c780ba910bf96220b5d7c80e5f830f71cc2e`
- captured live Runtime: `0.2.6.40`
- captured Houdini Adapter: `0.5.21`
- captured Knowledge tree: `a1a2e26e15c29e67860e375b1fdc194e27763ea6`

The latest development-source audit point remains Runtime **0.2.6.50**. The product repository has substantially integrated that delta, but the baseline must stay at the older commit until product validation, Setup/Supervisor reconciliation, Knowledge digest finalization, and changelog audit are complete.

Source synchronization follows `docs/DELTA_SYNC_WORKFLOW.md`.

## Product requirements

| Requirement | State |
|---|---|
| GitHub binding | **CORE PRESENT** — GitHub Bus transport and clean provisioning source migrated |
| Independent Knowledge Pack | **GENERATED / FINALIZATION PENDING** — clean promoted/generic output exists; canonical cross-platform digest and manifest remain |
| First-run independent GitHub Bus | **IN PROGRESS** — templates/provisioner/Setup frontend present; full rebased Setup routes pending |
| AI protocol onboarding | **FOUNDATION COMPLETE** — Bus README/read-first/index and public AI-agent protocol established |
| Auditable updates | **FOUNDATION COMPLETE** — CHANGELOG, change policy, baseline, Delta policy, remote pre/post checks |
| Shared product update source | **CONTRACT COMPLETE / SUPERVISOR INTEGRATION PENDING** — product source is `fmb22333-dev/AI-Bridge:main`; user Bus is separate |
| Runtime Core | **MAJOR FOUNDATION MIGRATED** — Core service, execution/recovery, persistence/security, V5 transport, BridgeAdmin slice present |
| Houdini Adapter | **MAJOR DELTA MIGRATED / FULL PRODUCT TEST PENDING** — current client/registry/capability kernel and new generic operations present |
| Installer/release assembly | **PENDING** |
| Fresh Windows + fresh Bus acceptance | **PENDING** |

## Product architecture that must remain invariant

The standalone product uses two authorities:

1. **Shared product repository** — `fmb22333-dev/AI-Bridge:main`
   - Runtime
   - Supervisor
   - host adapters
   - installer/setup
   - generic promoted Knowledge
   - protocol/specs
   - release authority

2. **Per-install user Bus**
   - `PROJECT_STATE_INDEX.json`
   - `AI_BRIDGE_READ_FIRST.md`
   - Bridge status/presence
   - V5 transport channels
   - user project authority

The following separations are mandatory:

- product updates default to `AI-Bridge:main`;
- `bridge.project.resume` reads the configured user Bus;
- a user Bus is never an implicit Runtime release source;
- productized source is never blindly overwritten by development SAFE COPY;
- raw local Staging/development Knowledge is not silently mirrored back into the product repository.

## Runtime / Adapter migration status

### Runtime foundation present

The product repository now contains the main Runtime foundation, including:

- protocol models;
- Runtime config/package entrypoints;
- Core service/control plane;
- execution budget policy;
- Adapter command/session/workspace management;
- host process control and plugin manager;
- checkpoint/evidence/snapshot/recovery persistence;
- Windows secret storage;
- local Adapter API;
- remote configuration/controller;
- result delivery and transport runner;
- GitHub Bus transport with V2/V3/V4 compatibility and V5 multi-channel;
- Runtime launcher and Houdini Adapter installer;
- Runtime app wiring for the current BridgeAdmin lifecycle;
- transport activity-heartbeat delta;
- Knowledge publish gate;
- detached publisher worker;
- Supervisor upgrade worker.

The product package metadata has been synchronized toward Runtime **0.2.6.50**. This is not yet declared a validated product release.

### BridgeAdmin product REBASE completed

`runtime/src/ai_bridge/adapters/bridge_admin.py` has been migrated with product-specific reconciliation rather than blind source copying.

Current contract:

- `_update_source()` defaults to shared `PRODUCT_REPOSITORY / PRODUCT_REF` = `fmb22333-dev/AI-Bridge:main`;
- `_bus_source()` separately resolves the user's configured Bus;
- `bridge.project.resume` uses `_bus_source()`;
- legacy implicit Bus bootstrap is disabled;
- `publish_source_mirror` defaults to false;
- validated artifact/manifest publication is separated from raw development source mirroring.

Future Delta Sync must treat this file as **REBASE REQUIRED** whenever source authority/update semantics change.

## Houdini Adapter status

The current product Adapter slice includes:

- package/bootstrap/version;
- `client.py`;
- `knowledge_registry.py`;
- dispatcher;
- inspection/context/batch inspection;
- parameter/code/node primitives;
- Cook/checkpoint/error handling;
- capability guidance;
- graph transaction/ensure engine;
- `compat_ops.py`;
- `inspect_ops.py`;
- `outcome.py`;
- `multiparm_ops.py`;
- `diagnostic_ops.py`;
- `geometry.query` support path.

The migrated generic semantic delta includes:

- `capability.search`;
- `inspect.parm_template`;
- `inspect.session_module`;
- `geometry.query`;
- `parm.multiparm.ensure`;
- `diagnostic.transaction`;
- normalized Adapter outcome handling.

The source slice corresponds to the current **Adapter 0.5.26** migration target. Full product regression has not yet been run, so this is migration state, not release acceptance.

## Tests migrated/rebased

The product repository now contains generic tests covering the migrated capability slice, including:

- `test_houdini_geometry_query.py`
- `test_houdini_multiparm_ensure.py`
- `test_houdini_diagnostic_transaction.py`
- `test_outcome_contract.py`
- Houdini capability integrity/search tests
- parm-template inspection tests
- session-module inspection tests
- transport activity-heartbeat regression
- detached publisher regression
- Supervisor upgrade regression
- product BridgeAdmin authority-separation regression
- project-resume regression rebased to generic Project A/B fixtures and `_bus_source()`

A new cross-platform Knowledge digest regression has also been added.

**Validation boundary:** these tests exist in the product repository, but full product `compileall + pytest` has not yet been executed for the reconciled repository. Do not report product regression PASS yet.

## Clean distributable Knowledge status

Raw development Knowledge was not copied. Clean output was generated using the product distribution filter.

Current generated output contains:

- 10 promoted authority entries;
- 1 generic validated/promoted template;
- 3 alias sets;
- 5 promoted capability-guidance entries;
- 16 error rules;
- 32 host rules;
- 6 distributable promoted recipes:
  - `code.safe_patch_and_cook`
  - `cook.checked`
  - `fbx.character_import.set_animation_and_cook`
  - `kinefx.import_with_frameinfo`
  - `network.ensure_and_cook`
  - `parm.safe_write_and_cook`

Explicitly excluded:

- `retarget.fbx_import_to_input_fix` — project-family;
- `network.build` — candidate;
- `network.build_and_cook` — deprecated.

The generated JSON payloads were checked against the intended Python filtering semantics and no project-family/current-project/developer-path contamination was found in that pass.

### Deterministic digest blocker

A cross-platform determinism defect was identified in the current Knowledge digest contract.

For the same logical filtered Knowledge:

- LF payload bytes produce:
  `6d1d6d67bd1601b816be3b4de2bc28df65f1dc8d225747b7f2a7be14af76c6f9`
- Windows CRLF payload bytes produce:
  `cdb49df281c07e67b6290fe380ef1e704d1c19ca0ee3c0a129fde40ee1e91841`

The CRLF value exactly matches the development 0.2.6.50 `distribution-release.json` digest.

Root cause: the generator writes JSON through platform-sensitive text output while `_content_digest()` hashes raw file bytes. Therefore the current digest is newline/platform dependent.

A regression test has been added that requires LF/CRLF-independent digest behavior, but the product implementation fix has **not yet been committed**. This issue must be resolved before generating the final product `distribution_manifest.json`.

## Web / Setup

Present:

- Web package entrypoint;
- Setup frontend;
- command-history page/script;
- Dashboard template;
- Setup frontend defaults shared Runtime source to `fmb22333-dev/AI-Bridge`.

Pending:

- full `web/routes.py` REBASE;
- remaining Dashboard static assets;
- Setup backend must persist `AI-Bridge:main + runtime-release.json` as product update authority while provisioning/connecting a separate user Bus.

The Setup product-source contract remains intentionally **RED** until the backend route integration is complete.

## Supervisor

Present:

- legacy 0.1.2 reference slice;
- 0.1.3 product update-source resolver;
- migrated Supervisor upgrade worker;
- regression coverage for the shared-product source model.

Pending integration must preserve mature Supervisor monitoring/activation/rollback behavior while removing legacy assumptions that:

1. derive Runtime update source from the user's Bus;
2. publish/bootstrap a `bridge-runtime` release branch inside the user's Bus.

Development Supervisor changes are therefore **REBASE REQUIRED**, not SAFE COPY.

## Delta classification

- **SAFE COPY:** ordinary generic Runtime/Adapter fixes and tests where product architecture has not diverged.
- **REBASE REQUIRED:** Supervisor/update source, Setup/provisioning, installer/release authority, Bus templates, BridgeAdmin/productized authority files.
- **KNOWLEDGE REGENERATE:** source Knowledge changes are re-filtered into a fresh clean distribution; never raw-copy the development Knowledge tree.
- **DO NOT DISTRIBUTE:** runtime state/history, credentials, Bridge IDs, local sessions/workspaces/paths, project authority/handoffs, caches, project-family/candidate knowledge as generic execution authority.

## Live isolation

This migration remains GitHub-product-source-only. It has not intentionally:

- restarted Houdini;
- modified a HIP;
- replaced the installed live Houdini Adapter;
- switched the developer's active Runtime;
- sent Host mutation commands;
- rewritten the developer Bus configuration.

## Next executable sequence

1. Finish the canonical LF/CRLF-independent Knowledge digest implementation and turn the new regression GREEN.
2. Generate/write the clean Knowledge `distribution_manifest.json` using the canonical digest contract.
3. Rebase full Setup `web/routes.py` and complete remaining Web assets.
4. Integrate the mature Supervisor body with the shared-product update-source resolver.
5. Audit remaining Runtime 0.2.6.50 dependency/test delta.
6. Run product `compileall`, targeted tests, then full `pytest`.
7. Re-read development source HEAD and classify any newly accumulated Delta.
8. Update CHANGELOG/status with actual validation evidence.
9. Only after reconciliation + validation + audit, advance `migration/BASELINE.json`.
10. Add product `runtime-release.json`, installer/release assembly and clean build flow.
11. Validate fresh Windows + fresh GitHub Bus installation.
12. Only after fresh-install acceptance should live developer update authority move away from the old development source.

## Acceptance boundary

The standalone repository is **not yet a release**.

The earlier blockers `client.py`, `knowledge_registry.py`, `bridge_admin.py`, outcome/multiparm/diagnostic support, and initial clean Knowledge generation are no longer pending. The remaining release blockers are now concentrated in:

- canonical deterministic Knowledge digest + manifest;
- Setup backend REBASE;
- Supervisor integration;
- complete product test validation;
- baseline advancement;
- installer/release assembly;
- fresh Windows + fresh Bus acceptance.
