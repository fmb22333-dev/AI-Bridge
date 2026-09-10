# AI Bridge Changelog

This is the human-readable product change log for `fmb22333-dev/AI-Bridge`.

It records meaningful changes to Runtime, Supervisor, host adapters, installer/setup, protocol, distributable Knowledge Pack, safety behavior, migration state, and release authority.

For logging rules, see `docs/CHANGE_POLICY.md`.

## Unreleased

### Runtime 0.2.6.50 migration reconciliation — 2026-09-10

#### Added
- Migrated current Houdini Adapter `client.py` and `knowledge_registry.py`.
- Migrated `outcome.py`, `multiparm_ops.py`, and `diagnostic_ops.py`.
- Synchronized the Adapter routing/inspection/compatibility slice required by `capability.search`, `inspect.parm_template`, `inspect.session_module`, `geometry.query`, `parm.multiparm.ensure`, and `diagnostic.transaction`.
- Migrated Runtime Knowledge publish gate, detached publisher worker, Supervisor upgrade worker, and transport activity-heartbeat delta.
- Added product-specific BridgeAdmin authority-separation regression.
- Rebased project-resume regression to generic Project A/B fixtures and user-Bus authority.
- Added generic regressions for geometry query, multiparm ensure, diagnostic transaction, Adapter outcome handling, capability integrity/search, parm-template/session-module inspection, publisher behavior, Supervisor upgrade, and transport activity heartbeat.
- Generated the first current Clean Knowledge payload set in the standalone product repository.
- Added a cross-platform Knowledge digest regression requiring LF/CRLF-independent digest behavior.
- Added `docs/HANDOFF_2026-09-10_RUNTIME_0.2.6.50_MIGRATION.md` for continuation from the reconciled state.

#### Changed
- Rebased `bridge_admin.py` so product update authority and user project/Bus authority are separate:
  - product updates default to `fmb22333-dev/AI-Bridge:main`;
  - `bridge.project.resume` reads the configured user Bus;
  - legacy implicit Bus bootstrap is disabled;
  - raw Staging/source mirroring is opt-in rather than default.
- Synchronized product package metadata toward Runtime **0.2.6.50** and Houdini Adapter **0.5.26** migration state.
- Clean Knowledge synchronization is now an actual generated product payload rather than only a planned filtering step.

#### Clean Knowledge
Generated clean product authority currently contains:
- 10 promoted authority entries;
- 1 generic template;
- 3 alias sets;
- 5 promoted capability-guidance entries;
- 16 error rules;
- 32 host rules;
- 6 promoted generic recipes:
  - `code.safe_patch_and_cook`
  - `cook.checked`
  - `fbx.character_import.set_animation_and_cook`
  - `kinefx.import_with_frameinfo`
  - `network.ensure_and_cook`
  - `parm.safe_write_and_cook`

Excluded from generic product execution authority:
- `retarget.fbx_import_to_input_fix` — project-family;
- `network.build` — candidate;
- `network.build_and_cook` — deprecated.

#### Deterministic-build finding
- Identified a cross-platform Knowledge digest defect: the same logical JSON payload hashes differently when stored with LF vs Windows CRLF.
- LF digest observed: `6d1d6d67bd1601b816be3b4de2bc28df65f1dc8d225747b7f2a7be14af76c6f9`.
- CRLF digest observed: `cdb49df281c07e67b6290fe380ef1e704d1c19ca0ee3c0a129fde40ee1e91841`.
- The CRLF digest exactly matches the development Runtime 0.2.6.50 distribution release.
- Root cause is platform-sensitive text serialization combined with raw-byte hashing.
- Regression coverage has been added; implementation remains intentionally unresolved until the next code step. Do not treat Clean Knowledge manifest generation as complete yet.

#### Validation status
- Full product `compileall + pytest` is still pending.
- Migrated tests are present but are not counted as product PASS until actually executed against the reconciled product repository.
- Setup backend product-source regression remains intentionally RED until full `web/routes.py` REBASE.
- New cross-platform Knowledge digest regression is intentionally RED against the current raw-byte digest implementation.
- The migration baseline remains unchanged until reconciliation, validation, synchronization, and audit are complete.

#### Pending
- Canonicalize generated Knowledge serialization/digest across LF/CRLF and write final `distribution_manifest.json`.
- Rebase full Setup `web/routes.py` to `AI-Bridge:main + runtime-release.json`; finish remaining Dashboard assets.
- Integrate mature Supervisor monitoring/activation/rollback with the shared-product update-source resolver and remove Bus-as-Runtime-source fallback/publication.
- Audit remaining Runtime 0.2.6.50 dependency/test delta.
- Run targeted/full product regression and `compileall`.
- Re-read development source HEAD and classify any newly accumulated Delta.
- Advance `migration/BASELINE.json` only after successful validation and audit.
- Add product release manifest/installer assembly and validate fresh Windows + fresh GitHub Bus installation.

### Runtime 0.2.6.50 delta audit — 2026-09-09
- Audited the development source from migration baseline `03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e` through audit head `42b2492565a5bb3b15e0cd2df3cb216ecae297ab`: **86 commits ahead / 0 behind** at that audit point.
- Confirmed source mirror and live Bridge reported **0.2.6.50** at the audit point; V5 multi-channel transport was active and the live status reported no active Houdini sessions.
- Added `migration/DELTA_AUDIT_2026-09-09_RUNTIME_0.2.6.50.md` with SAFE COPY / REBASE REQUIRED / KNOWLEDGE REGENERATE / DO NOT DISTRIBUTE classification.
- Began the generic Houdini capability slice with bounded `geometry.query` semantics.
- Baseline intentionally remained at `03b24b41...` pending full integration and validation.

### Semantic-completeness delta — 2026-09-09
- Accepted development-source commit `33b467a723aa635824c744a29b638fce7b36b53e` as an early semantic-completeness delta.
- Initial SAFE COPY synchronized Adapter version metadata, `compat_ops.py`, `dispatcher.py`, and `inspect_ops.py`.
- That initial delta added `capability.search` discovery/integrity logic and read-only `inspect.parm_template` support.
- Dependencies that were still pending at that moment (`client.py`, `knowledge_registry.py`, generated Clean Knowledge) were completed in the 2026-09-10 reconciliation above.

### Added
- Independent `AI-Bridge` product repository separated from the developer Bus.
- Canonical AI onboarding contract: generated Bus repositories point agents to `PROJECT_STATE_INDEX.json` first.
- Generated Bus root README bootstrap hint.
- Clean GitHub Bus provisioning source retargeted to `fmb22333-dev/AI-Bridge`.
- Clean distributable Knowledge Pack filtering source.
- Mandatory remote preflight/recheck workflow for multi-human / multi-AI updates.
- Canonical `CHANGELOG.md` plus `docs/CHANGE_POLICY.md`.
- Machine-readable continuous migration baseline at `migration/BASELINE.json` plus `docs/DELTA_SYNC_WORKFLOW.md`.
- Runtime foundation: protocol, Core service/recovery control plane, persistence, security, execution policy, host/plugin process control, local/remote transport, result delivery, GitHub V2/V3/V4 compatibility and V5 multi-channel transport.
- Houdini Adapter foundation and package manifest.
- Runtime launch and Houdini Adapter installation scripts.
- Setup UI and command-history Web migration.
- Supervisor product update-source resolver.

### Changed
- Product/runtime/protocol authority is being migrated from `ai-bridge-bus:bridge-runtime` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared product/protocol authority.
- AI maintenance workflow requires changelog review/update, migration-baseline reconciliation, and remote pre/post checks.
- Supervisor update-source semantics are explicit: the user Bus is never an implicit Runtime release source.
- Setup UI product-source fallback points to `fmb22333-dev/AI-Bridge`, while each user keeps an independent Bus.
- Continuous synchronization classifies changes as SAFE COPY, REBASE REQUIRED, KNOWLEDGE REGENERATE, or DO NOT DISTRIBUTE.

### Migration baseline
- Development source baseline: `fmb22333-dev/ai-bridge-bus:bridge-runtime@03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e`.
- Baseline remains intentionally unchanged until the reconciled Runtime 0.2.6.50 product slice passes validation and audit.

### Safety
- Public/distributable content excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family/candidate knowledge as generic execution authority.
- Repository migration remains isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` branch inside a user's Bus is forbidden.
- Productized files are not blindly overwritten during Delta Sync; architecture-sensitive changes require reconciliation.

## 2026-09-09 — Repository bootstrap
- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
