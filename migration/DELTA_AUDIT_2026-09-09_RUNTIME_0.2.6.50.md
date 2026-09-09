# Development Delta Audit — Runtime 0.2.6.50

**Date:** 2026-09-09  
**Product repository:** `fmb22333-dev/AI-Bridge:main`  
**Development source:** `fmb22333-dev/ai-bridge-bus:bridge-runtime`

## Authority snapshot

The migration baseline currently starts at development commit:

- old baseline: `03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`
- current development head: `42b2492565a5bb3b15e0cd2df3cb216ecae297ab`
- delta: **86 commits ahead / 0 behind**
- current source mirror version: **0.2.6.50**
- current live Bridge version: **0.2.6.50**
- live transport: `issue_channel_v5`, multi-channel enabled
- live status at audit: no active Houdini sessions; `write_blocked=false`

This is a migration/audit snapshot only. It does not activate or replace the developer Runtime.

## Material delta classes

### SAFE COPY candidates

Generic Runtime / Adapter implementation added or materially expanded since the old baseline:

- Houdini `geometry.query`
- Houdini `parm.multiparm.ensure`
- Houdini `diagnostic.transaction`
- Houdini `inspect.parm_template`
- Houdini `inspect.session_module`
- capability search / integrity support
- normalized Adapter outcome contract
- transport activity heartbeat
- generic regression tests for the above capabilities
- generic Knowledge publication gate implementation/tests

These may be migrated only with their required dependencies and corresponding regression tests.

### REBASE REQUIRED

The following development-source files cannot be copied blindly because the standalone product repository has a different product/Bus authority model:

- Supervisor 0.1.3 / 0.1.4
- Runtime update source logic
- self-bootstrap publisher
- Supervisor upgrade worker
- Setup/provisioning defaults
- release manifests
- installer/release-source logic

Important finding: development Supervisor 0.1.4 still contains the legacy fallback that derives the Runtime update repository from the user's Bus and sets `bootstrap_from_bus=true`. The standalone product contract forbids this behavior. Product Supervisor integration must retain the `AI-Bridge:main` shared-product resolver and transplant only the newer generic Supervisor mechanics.

### KNOWLEDGE — REGENERATE, DO NOT RAW COPY

The source Knowledge tree changed materially after the old baseline. Promotion authority now includes additional capability/recipe lifecycle information, including network recipe corrections and new semantic capability guidance.

Rules for this sync:

1. Raw development Knowledge is **not** distributable authority.
2. Project-family/candidate evidence remains excluded.
3. Only generic entries satisfying the public promotion filter may enter the clean Knowledge Pack.
4. The clean pack must be regenerated from the latest reconciled source tree after Runtime/Adapter migration.
5. The old clean Knowledge output must not be incrementally patched as if it were source authority.

### DO NOT DISTRIBUTE

The delta also contains large project-specific state changes, especially Retarget V19 authority/history. These remain development evidence only and must not enter the standalone product repository as generic Bridge authority.

Excluded examples:

- `HOUDINI_RETARGET_V19_CURRENT_STATE.md`
- project handoffs/current-state documents
- live Bridge status/session/workspace data
- command/result/recovery history
- developer machine paths and project identifiers
- project-family candidate Knowledge

## Migration order from this audit

1. Rebase public execution/learning specs against the new generic semantics.
2. Migrate generic Runtime dependencies required by the new Houdini capabilities.
3. Migrate Houdini Adapter kernel changes and their tests.
4. Integrate Supervisor 0.1.4 mechanics into the standalone product model without the Bus-as-Runtime-source fallback.
5. Complete Setup `routes.py` product-source rebase.
6. Run targeted + full repository regression.
7. Regenerate the clean Knowledge Pack from the reconciled latest source.
8. Only after successful validation, advance `migration/BASELINE.json` from `03b24b41...` to the reconciled development commit.

## Baseline advancement gate

**Do not advance the baseline yet.**

The development source is stable enough to begin this delta migration, but baseline advancement means the delta has been reconciled and validated in the standalone repository. At this audit point it has only been classified.

## Validation status

- development/live Runtime agreement at audit: **PASS — 0.2.6.50**
- transport stable/readable: **PASS**
- source delta enumerated: **PASS — 86 commits**
- project-state contamination filter: **DEFINED**
- Supervisor product-model conflict detected: **PASS / REBASE REQUIRED**
- standalone Runtime integration of this delta: **NOT YET COMPLETE**
- regenerated clean Knowledge Pack: **NOT YET RUN**
- standalone full regression: **NOT YET RUN**
