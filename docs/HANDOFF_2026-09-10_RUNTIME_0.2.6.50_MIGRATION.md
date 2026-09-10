# AI Bridge Runtime 0.2.6.50 Migration Handoff

**Prepared:** 2026-09-10
**Product repository:** `fmb22333-dev/AI-Bridge:main`
**Development source:** `fmb22333-dev/ai-bridge-bus:bridge-runtime`
**Purpose:** allow the next AI/conversation to continue the standalone product migration without repeating completed work or touching the developer's live Houdini environment.

## Mandatory recovery order

Before doing any further migration work:

1. Read `fmb22333-dev/ai-bridge-bus:main:PROJECT_STATE_INDEX.json` first.
2. Read the live Bridge status referenced by that index if any decision depends on live Runtime/Adapter/session state.
3. Read this handoff, `docs/MIGRATION_STATUS.md`, `migration/BASELINE.json`, and `CHANGELOG.md` in the product repository.
4. Treat live runtime status > project current state > normative specs > handoff/chat as authority.
5. Do not repository-wide search while indexed authority pointers are valid.
6. Keep migration GitHub-file-only unless the user explicitly authorizes activation/testing against the live Runtime or Houdini.

## Product architecture that must be preserved

The standalone product uses two repositories:

- **Shared product repository:** `fmb22333-dev/AI-Bridge` — Runtime, Supervisor, host adapters, installer/setup, generic promoted Knowledge, protocol/specs and release authority.
- **Per-install user Bus:** generated/selected during first-run — `PROJECT_STATE_INDEX.json`, `AI_BRIDGE_READ_FIRST.md`, status/presence, transport channels and user project authority.

These authorities must remain separate:

- product update source defaults to `fmb22333-dev/AI-Bridge:main`;
- `bridge.project.resume` and project authority read the user's Bus;
- a user's Bus must never become an implicit Runtime release source;
- raw local Staging/development Knowledge must not silently overwrite product source.

## Work completed in the latest migration slice

### Runtime / Houdini Adapter synchronization

The product source has been advanced toward development Runtime **0.2.6.50** and Houdini Adapter **0.5.26**.

Migrated/synchronized generic implementation includes:

- `geometry.query` support path and current Adapter routing;
- `parm.multiparm.ensure` via `multiparm_ops.py`;
- `diagnostic.transaction` via `diagnostic_ops.py`;
- normalized Houdini outcome contract via `outcome.py`;
- current `client.py` capability/session layer;
- current `knowledge_registry.py`;
- current Adapter `__init__.py`, `dispatcher.py`, `compat_ops.py`, `inspect_ops.py`;
- Runtime `app.py` current BridgeAdmin lifecycle wiring;
- transport `remote_controller.py` and `runner.py` activity-heartbeat delta;
- Knowledge publish gate;
- detached publisher worker;
- Supervisor upgrade worker;
- `runtime/pyproject.toml` synchronized to Runtime 0.2.6.50 metadata.

Files such as `service.py`, `github_bus.py`, and `result_delivery.py` were checked against the old migration baseline; their source HEAD differences were not treated as new semantic delta when the development files had not changed since the baseline.

### BridgeAdmin product REBASE

`runtime/src/ai_bridge/adapters/bridge_admin.py` was not blindly copied. It was rebased for the standalone product architecture.

Important resulting contract:

- `_update_source()` defaults to shared `PRODUCT_REPOSITORY` / `PRODUCT_REF` (`fmb22333-dev/AI-Bridge:main`).
- `_bus_source()` separately resolves the configured user Bus.
- `bridge.project.resume` uses `_bus_source()`, not `_update_source()`.
- legacy implicit Bus bootstrap is disabled.
- `publish_source_mirror` defaults to `false`.
- runtime/distribution publication does not copy raw `Runtime/Staging` source back into the product repository unless an explicit source-mirror mode is deliberately enabled.

This separation is a product-specific architectural requirement and must not be overwritten by a future SAFE COPY from the development Bus.

### Tests migrated/rebased

Migration added/synchronized tests covering the new Houdini capabilities and supporting contracts, including:

- `test_houdini_geometry_query.py`
- `test_houdini_multiparm_ensure.py`
- `test_houdini_diagnostic_transaction.py`
- `test_outcome_contract.py`
- capability integrity/search tests
- parm-template/session-module inspection tests
- transport activity heartbeat regression
- detached publisher regression
- Supervisor upgrade regression
- product-specific BridgeAdmin authority-separation regression
- project-resume regression rebased to generic Project A/B fixtures and `_bus_source()` authority.

Project-specific AutoUV/Retarget fixtures were intentionally removed from the product project-resume test.

**Important:** these files have been migrated, but the product repository has not yet completed a full `compileall + pytest` run for this reconciled slice. Do not report product regression PASS until that is actually executed.

## Clean distributable Knowledge generated

Raw development Knowledge was **not copied** into the product repository. A clean distribution was generated according to the current `knowledge_pack.py` filtering contract.

Current generated clean Knowledge contains:

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

Excluded from the clean product authority:

- `retarget.fbx_import_to_input_fix` — project-family recipe;
- `network.build` — candidate, not promoted;
- `network.build_and_cook` — deprecated.

The generated 12 JSON payload files were independently checked against the current Python filtering semantics and matched the expected filtered text. Forbidden project/developer literals checked in this pass were absent, including project-family scopes, historical path/name metadata, AutoUV/Retarget identifiers and developer Houdini paths.

## Newly discovered deterministic-digest issue

This is the immediate technical issue to fix before declaring Clean Knowledge complete.

Development `distribution-release.json` for Runtime 0.2.6.50 reports clean Knowledge digest:

`cdb49df281c07e67b6290fe380ef1e704d1c19ca0ee3c0a129fde40ee1e91841`

The same generated JSON payloads stored in GitHub with LF line endings produce:

`6d1d6d67bd1601b816be3b4de2bc28df65f1dc8d225747b7f2a7be14af76c6f9`

Recomputing those exact product payloads as CRLF produces exactly the development release digest:

`cdb49df281c07e67b6290fe380ef1e704d1c19ca0ee3c0a129fde40ee1e91841`

Therefore the current deterministic Knowledge digest is platform/newline dependent. The likely cause is `Path.write_text()` using platform newline translation on Windows while `_content_digest()` hashes raw file bytes.

### Required fix

Make Knowledge serialization/digest platform independent. Preferred direction:

- force canonical LF bytes when writing generated JSON (for example, explicit byte writing or an explicit newline contract), and/or normalize newlines before digesting;
- add a regression proving the same logical Knowledge produces the same digest independent of Windows CRLF vs LF;
- choose one canonical digest contract and regenerate `distribution_manifest.json` accordingly.

Do not mutate clean Knowledge content merely to chase the historical Windows digest. The logical filtered content is already verified; the problem is byte canonicalization.

## Current baseline state

`migration/BASELINE.json` still intentionally points to the old development baseline:

`03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`

Do **not** advance it yet.

Reason: the Runtime 0.2.6.50 delta is substantially integrated, but product validation, Setup/Supervisor reconciliation, deterministic Knowledge finalization and changelog audit are not complete. The baseline policy requires reconciliation + validation + synchronization + audit before advancing.

## Documents currently stale and needing update

`docs/MIGRATION_STATUS.md` and `CHANGELOG.md` still describe several items as pending that were completed in this latest slice, including `client.py`, `knowledge_registry.py`, `bridge_admin.py`, outcome/multiparm/diagnostic support and clean Knowledge generation.

They must be updated after the deterministic Knowledge fix and product validation status are known. Do not treat their older pending lists as proof that these files are still missing; this handoff records the newer work.

## Next executable sequence

Recommended continuation order:

1. Fix the LF/CRLF deterministic Knowledge digest issue and add regression coverage.
2. Generate/write the clean Knowledge `distribution_manifest.json` using the canonical digest contract.
3. Rebase full Setup `web/routes.py` so shared product updates use `AI-Bridge:main` + `runtime-release.json`, while first-run still provisions an independent user Bus.
4. Integrate the product update-source resolver with the mature Supervisor body; preserve current Supervisor process monitoring/activation/rollback, but remove Bus-as-Runtime-source fallback/publication behavior.
5. Audit remaining Runtime 0.2.6.50 dependency files and generic tests against the development source; SAFE COPY only ordinary generic files, REBASE architecture-sensitive product files.
6. Run `compileall` and the relevant targeted/full `pytest` suite in a real checkout/build environment. Do not infer PASS from development-source tests.
7. Update `docs/MIGRATION_STATUS.md` and `CHANGELOG.md` with the actual reconciled/validated state.
8. Re-read development source HEAD and classify any new delta that appeared while migration was running.
9. Only after successful reconciliation/validation/audit, advance `migration/BASELINE.json` to the reconciled source commit.
10. Add product `runtime-release.json`, installer assembly/release manifest flow, then build and validate a clean Windows + fresh GitHub Bus installation.
11. Only after fresh-install acceptance should live developer update authority move away from the old development source.

## Safety / non-actions in this migration slice

The latest migration work was GitHub-product-source-only. It did not intentionally:

- restart Houdini;
- modify a HIP;
- replace the live installed Houdini Adapter;
- switch the developer's active Runtime;
- send Host mutation commands;
- rewrite the developer Bus configuration.

Continue to preserve this isolation unless the user explicitly requests live activation/acceptance testing.

## Acceptance status

The standalone repository is **not yet a release**.

The Runtime/Houdini/Knowledge migration is materially further along than `docs/MIGRATION_STATUS.md` currently states, but the remaining blockers are real:

- canonical deterministic Knowledge digest;
- full Setup backend REBASE;
- mature Supervisor integration under the shared-product update model;
- full product compile/test validation;
- baseline advancement/audit;
- installer/release assembly;
- fresh Windows + fresh Bus acceptance.
