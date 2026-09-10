# AI Bridge Changelog

This is the human-readable product change log for `fmb22333-dev/AI-Bridge`.

It records meaningful changes to Runtime, Supervisor, host adapters, installer/setup, protocol, distributable Knowledge Pack, safety behavior, migration state, and release authority.

For logging rules, see `docs/CHANGE_POLICY.md`.

## Unreleased

### Runtime 0.2.6.52 fallback multi-ingress transport — 2026-09-11

- Kept Issue Comment V5 as the primary GitHub command ingress and promoted the existing GitHub Contents command directory into a simultaneously polled fallback ingress, without adding a new relay service, daemon, credential type, or external dependency.
- Multiple ingress observations for the same canonical command are persisted as independent receipts while `command_id` remains the single execution identity.
- The same `command_id` with divergent canonical payloads now fails closed with `COMMAND_IDENTITY_CONFLICT` rather than risking ambiguous execution.
- Durable transport acceptance is shared across Comment and Contents ingress. Pure Contents fallback after Comment discovery is unavailable retains the same pre-accept/receipt reliability semantics.
- A single terminal `ExecutionResult` is fanned out independently to all observed receipts. Failed receipt publication remains pending and is retried on later polls without re-executing host-side effects.
- Transport status reporting is mode-accurate: V5 reports multi-ingress Comment + Contents; Contents-only and legacy V2/V3/V4 paths do not falsely report simultaneous ingress.
- Added public regression coverage for atomic command claims, divergent-payload conflicts, dual ingress merge, pure Contents fallback, per-receipt fanout/retry, terminal-result replay, and transport-state reporting.
- Runtime package, Presence, Dashboard/static cache, and release artifact labels are updated to `0.2.6.52`.
- Houdini Adapter remains `0.5.26`; Supervisor remains `0.1.4`; distributable Knowledge authority is unchanged by this Runtime-only delta.
- Private development source merged at `<private-development-source>@1ea396efd020525ec6fe6c817c9cb2f58c027d58` after final committed-source Windows validation run `34523572329`: `compileall` PASS, fallback/V5 targeted **24/24 PASS**, full Runtime **344/344 PASS**.
- The prior migration interval from `f04df88ddf29791af8c3761786cb5650e10e594a` to the pre-feature source head contained only project-specific documents and private generated release metadata; those items were classified `do_not_distribute` and were not copied into the product repository.
- Initial public PR #6 product validation run `34525306816` passed with **109/109 pytest PASS**, deterministic release build PASS, compile PASS, release verifier PASS, and PowerShell parser PASS.
- After that gate, the migration baseline advanced from `f04df88ddf29791af8c3761786cb5650e10e594a` to `1ea396efd020525ec6fe6c817c9cb2f58c027d58`.
- Final public PR #6 validation run `34525705706` passed on release-candidate head `638a071f68d46db9736595dc14aa977bd389758b` with **109/109 pytest PASS**, deterministic release build PASS, compile PASS, release verifier PASS, and PowerShell parser PASS.
- PR #6 merged to `main` at `c99058ff52633f5ba3ec66482171a96b0d8e5f5b` on `2026-09-10T20:25:21Z`.
- Final `main` product validation run `34526280715` passed, including generated release artifact publication and fresh Windows installation from the public GitHub branch.
- Generated artifact commit: `ca3459ce3c82a3e2783abe4aac9edf6233111fbe`.
- Published Runtime release source commit: `638a071f68d46db9736595dc14aa977bd389758b`.
- Published Runtime bundle SHA-256: `acf89d2be14d9dc85319938271ff6f8b441ed6cd288c8f9a26cbdcb99c889966`; installer bundle remains `4db513b7976c1eccd9e08418454a6404f267896ee33f1fea81a6fa3121de1a5c`; clean Knowledge digest remains `f2fabe69cad95c9722a285c2fa5f91f91837a75d9dda73a8d236d53351fcf093`.
- Runtime 0.2.6.52 public source/release synchronization is complete. The developer's active installed Runtime remains 0.2.6.51 because this synchronization intentionally did not switch the live installation; explicit real end-user environment acceptance remains a separate gate.

### Runtime 0.2.6.51 V5 transport reliability — 2026-09-11

- Reconciled the generic Runtime transport delta from the private development source into the standalone public product without copying live Bus state, project documents, machine paths, credentials, Bridge IDs, sessions, or command history.
- Changed V5 delivery to persist a durable `transport_accepted` command record before publishing ACK, closing the ACK-before-durable-state crash window.
- V5 ACK now carries the validated command envelope so an accepted command can be reconstructed after Runtime restart.
- Existing terminal DB results can be republished without re-executing host or external side effects.
- Invalid V5 command envelopes now receive an explicit `AI_BRIDGE_NACK_V5` instead of being silently discarded.
- Added Issue #1 fallback discovery when the repository-wide 100-comment window is saturated and contains no executable command.
- Preserved nested and flat V5 sender compatibility and V2/V3/V4 compatibility paths.
- Added public regression coverage for explicit ACK semantics, transient ACK failure, saturated discovery, durable acceptance, terminal-result replay, malformed-envelope NACK, and ACK restart recovery.
- Updated Runtime/Dashboard/static cache version markers and the product release workflow to `0.2.6.51`.
- Houdini Adapter, Supervisor, and distributable Knowledge source authority are unchanged by this Runtime-only delta.
- Private source validation before synchronization: `compileall` PASS and **333/333 pytest PASS**.
- Public Windows product validation passed twice on PR #4; final PR run `34510120521` completed successfully with **98/98 pytest PASS**, compile PASS, deterministic release build PASS, release verifier PASS, and PowerShell parser PASS.
- PR #4 merged to `main` at `720f70fd10bd64355dc1d12c7a06c3ec1746931c` on 2026-09-10T17:46:54Z.
- Final `main` validation run `34510305457` passed, including generated release artifact publication and fresh Windows install from the public GitHub branch.
- Generated artifact commit: `8c96c940d50967f31d0298a04289976d267d17d6`.
- Published Runtime bundle SHA-256: `dd55da82b4e332bc753d9e2ac0ef7b0f1a58fe582c621b4bba5b0207ff4f1a71`; installer bundle remains `4db513b7976c1eccd9e08418454a6404f267896ee33f1fea81a6fa3121de1a5c`; clean Knowledge digest remains `f2fabe69cad95c9722a285c2fa5f91f91837a75d9dda73a8d236d53351fcf093`.
- Migration baseline advanced from `42b2492565a5bb3b15e0cd2df3cb216ecae297ab` to `f04df88ddf29791af8c3761786cb5650e10e594a`. The Runtime 0.2.6.51 public source/release synchronization is complete; explicit real end-user environment acceptance remains a separate gate.

### Public repository hygiene — 2026-09-10

- Replaced private development-source, Bridge-ID, project-family, and machine-path references in public-facing migration/audit documentation with generic placeholders.
- Generalized legacy Bus leak guards so product validation rejects any `ai-bridge-bus` release-source dependency rather than one owner-specific repository.
- Replaced project-name denylisting in distributable Knowledge filtering with a positive generic scope allowlist for the current Houdini product surface.
- Generalized Dashboard workspace examples, synchronized static cache keys to Runtime 0.2.6.50, and restored the recent-command summary to five entries.
- Added `.gitignore` rules for local state, credentials, caches, logs, databases, and build outputs.
- Added `SECURITY.md` with public repository hygiene/reporting guidance.
- Moved product release validation from the historical release-assembly branch to `main`.

### Runtime 0.2.6.50 standalone product assembly — 2026-09-10

#### Completed
- Reconciled the development source through `<private-development-source>@42b2492565a5bb3b15e0cd2df3cb216ecae297ab`.
- Product Runtime is **0.2.6.50**; Houdini Adapter is **0.5.26**; Supervisor is **0.1.4**.
- Rebased Setup backend and BridgeAdmin so shared product update authority is `fmb22333-dev/AI-Bridge:main`, while every user Bus remains separate transport/project authority.
- Integrated Supervisor 0.1.4 monitoring/rollback with the standalone product update model and fail-closed rejection of Bus Runtime bootstrap/publication.
- Completed Dashboard asset migration.
- Fixed cross-platform Knowledge determinism by canonicalizing generated text/digest behavior.
- Removed dangling project-family recipe authority from the clean product registry.
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
- Final push validation run: `34431007029`
- Final PR validation run: `34431009451`
- Generated artifact synchronization commit: `9655e8ab51ef77ec9b961fd638352b0adaeed692`
- Final release-candidate head: `3ace65d300b4de459f8dec2215a0379d6b7abdc6`
- Merged to `main` by PR #2 at merge commit `35a3bce81adcb1a68906304c025bc40db9f6dadb`.
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
- Product/runtime/protocol authority is being migrated from `private development source` into this repository.
- New per-install Bus templates reference `fmb22333-dev/AI-Bridge:main` for shared product/protocol authority.
- AI maintenance workflow requires changelog review/update, migration-baseline reconciliation, and remote pre/post checks.
- Supervisor update-source semantics are explicit: the user Bus is never an implicit Runtime release source.
- Setup UI product-source fallback points to `fmb22333-dev/AI-Bridge`, while each user keeps an independent Bus.
- Continuous synchronization classifies changes as SAFE COPY, REBASE REQUIRED, KNOWLEDGE REGENERATE, or DO NOT DISTRIBUTE.

### Migration baseline
- Development source baseline: `<private-development-source>@1ea396efd020525ec6fe6c817c9cb2f58c027d58`.
- Runtime 0.2.6.52 public source/release synchronization is complete: final PR validation, `main` artifact publication, release verification, and fresh Windows GitHub install smoke all passed. The active developer Runtime remains 0.2.6.51 because this synchronization did not switch the live installation; explicit real-environment end-to-end acceptance remains separately pending.

### Safety
- Public/distributable content excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family/candidate knowledge as generic execution authority.
- Repository migration remains isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` branch inside a user's Bus is forbidden.
- Productized files are not blindly overwritten during Delta Sync; architecture-sensitive changes require reconciliation.

## 2026-09-09 — Repository bootstrap
- Created the dedicated `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
