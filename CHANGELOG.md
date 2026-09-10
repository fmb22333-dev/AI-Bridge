# AI Bridge Changelog

This is the human-readable product change log for `fmb22333-dev/AI-Bridge`.

It records meaningful changes to Runtime, Supervisor, host adapters, installer/setup, protocol, distributable Knowledge Pack, safety behavior, migration state, and release authority.

For logging rules, see `docs/CHANGE_POLICY.md`.

## Unreleased

### Runtime 0.2.6.50 standalone product assembly — 2026-09-10

#### Completed
- Reconciled the development source through `fmb22333-dev/ai-bridge-bus:bridge-runtime@42b2492565a5bb3b15e0cd2df3cb216ecae297ab`.
- Product Runtime is **0.2.6.50**; Houdini Adapter is **0.5.26**; Supervisor is **0.1.4**.
- Rebased Setup backend and BridgeAdmin so shared product update authority is `fmb22333-dev/AI-Bridge:main`, while every user Bus remains separate transport/project authority.
- Integrated Supervisor 0.1.4 monitoring/rollback with the standalone product update model and fail-closed rejection of Bus Runtime bootstrap/publication.
- Completed Dashboard asset migration.
- Fixed cross-platform Knowledge determinism by canonicalizing generated text/digest behavior.
- Removed dangling project-family Retarget recipe authority from the clean product registry.
- Generated clean distributable Knowledge with six promoted generic recipes and no candidate/deprecated/project-family execution authority.
- Added deterministic release builder/verifier, `runtime_bundle.zip`, `runtime-release.json`, `supervisor-release.json`, `release-manifest.json`, `INSTALL_AI_BRIDGE.ps1`, `INSTALL_AI_BRIDGE.bat`, and `AI_Bridge_Installer.zip`.
- Installer verifies SHA-256, preserves Current/Previous Runtime generations, writes standalone product update authority, and never uses the user's Bus as an implicit Runtime source.
- Added bounded GitHub read retries and corrected PowerShell URL interpolation for private-product-repository installation.
- Added serialized/cancel-in-progress Windows product release validation.
- Runtime release `source_commit` is now derived from the last commit touching `runtime/`, so documentation-only commits no longer churn release metadata or produce bot-head PR loops.

#### Validation evidence
- Windows `compileall`: **PASS**
- Full product `pytest`: **84/84 PASS**
- Release verifier: **PASS**
- PowerShell parser: **PASS**
- Generated artifact publication: **PASS**
- Fresh Windows directory install by re-downloading from the GitHub validation branch: **PASS**
- Final push validation run: `34430874302`
- Final PR validation run: `34430877409`
- Generated artifact synchronization commit: `9655e8ab51ef77ec9b961fd638352b0adaeed692`
- Runtime bundle SHA-256: `00ee9abd2b5533667c86256b464725d7c284d5841e2a8cb8b57ad5f4f19a822e`
- Installer bundle SHA-256: `4db513b7976c1eccd9e08418454a6404f267896ee33f1fea81a6fa3121de1a5c`
- Clean Knowledge digest: `f2fabe69cad95c9722a285c2fa5f91f91837a75d9dda73a8d236d53351fcf093`

#### Baseline
- Migration baseline advanced from `03b24b41c03d2358afc89e91ed3d4b0cf6e1da4e` to `42b2492565a5bb3b15e0cd2df3cb216ecae297ab`.
- Future product synchronization compares development Delta from the new baseline.

#### Remaining acceptance only
The code/release migration is complete. The only remaining release gate intentionally not executed in this work is real end-user environment acceptance:
- provision a genuinely new GitHub Bus through Setup UI;
- complete interactive GitHub authorization;
- connect the fresh Bus to the fresh Runtime;
- install/activate the Houdini Adapter there;
- verify a live Houdini round-trip.

Do not conflate the already-passed clean Windows installer smoke test with this explicit real-environment acceptance.

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
- Development source baseline: `fmb22333-dev/ai-bridge-bus:bridge-runtime@42b2492565a5bb3b15e0cd2df3cb216ecae297ab`.
- Runtime 0.2.6.50 standalone product reconciliation and validation are complete; only explicit real-environment end-to-end acceptance remains.

### Safety
- Public/distributable content excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family/candidate knowledge as generic execution authority.
- Repository migration remains isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` branch inside a user's Bus is forbidden.
- Productized files are not blindly overwritten during Delta Sync; architecture-sensitive changes require reconciliation.

## 2026-09-09 — Repository bootstrap
- Created the dedicated private `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
