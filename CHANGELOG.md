# AI Bridge Changelog

This is the human-readable product change log for `fmb22333-dev/AI-Bridge`.

It records meaningful changes to Runtime, Supervisor, host adapters, installer/setup, protocol, distributable Knowledge Pack, safety behavior, migration state, and release authority.

For logging rules, see `docs/CHANGE_POLICY.md`.

## Unreleased

### Runtime 0.2.6.89 non-blocking Step 2 + persisted Step 4 authority — 2026-09-21

- Fixed the remaining fresh-install case where Runtime 0.2.6.88 could still leave Step 2 apparently stuck and Step 4 permanently disabled.
- Step 2 provisioning is now a Runtime-owned background transaction. The UI submits `/setup/provision/start` and then polls `/setup/provision/state`; browser request lifetime no longer owns GitHub repository provisioning.
- Concurrent/repeated Step 2 clicks attach to the active task rather than starting duplicate repository mutations. The provisioning path remains idempotent against an already-created clean Bus.
- First-connect GitHub configuration no longer requires an immediate Presence write before persisting the valid Bus config. Repository branch health is verified, then `remote.json`/credential/Bridge ID are persisted and the GitHub runner performs Presence/message-mode work asynchronously with its normal retry semantics.
- Step 4 now treats persisted `remote.json` as GitHub authority for unlock/Bridge ID, instead of depending only on transient in-memory `runtime_state.remote.configured`. This matches the existing Supabase `_primary_bridge_id` authority behavior.
- The Setup client has a bounded 120-second observation window with an explicit recover/retry message rather than an indefinitely disabled button.

### Runtime 0.2.6.88 Step 2 provisioning progress + Step 4 unlock repair — 2026-09-21

- Fixed the fresh-install report where Step 2 appeared permanently stuck even after GitHub repository/token work had already succeeded, while Step 4 stayed disabled.
- Root cause: Step 2 serialized repository initialization plus transport setup behind one opaque request, so the GitHub repository could already exist while later Index/README/Issue/Presence work was still running. Step 4 also loaded GitHub configuration only once.
- Added explicit Setup provisioning stages for credential resolution, identity, repository check/create, safety check, PROJECT_STATE_INDEX, READ_FIRST, README, Issue #1, GitHub connection and update-source persistence. The Setup page polls this progress every 500 ms.
- Removed GitHub Issue Comment/mailbox discovery from the first-connect critical path. Initial setup still verifies branch access and writes Presence; message-mode discovery occurs on the transport runner's first poll.
- Step 4 now rechecks GitHub Bus configuration every 1.5 seconds and automatically enables Supabase setup when Step 2 reaches configured=true; the disabled button explains that it is waiting for Step 2.
- Updated the Supabase extension wrapper to forward configure_github keyword options, preserving the new deferred-probe setup path through the existing 0.2.6.x compatibility layer.
- PR #19 validation Run `35576787278` passed all product gates.
- Main Run `35576925917` passed end-to-end: **516/516 pytest PASS**, release verifier PASS, generated artifact publication PASS, fresh GitHub install PASS and bundled offline dependency bootstrap PASS.
- Published Runtime bundle SHA-256: `65e2f98c1591107174d671c847fdbe021bb3c8ecd53544eb1be1ab65c3733301`; generated artifact commit: `e786105bc25e11758d2833d61bea428a41ca3fd4`.

### Runtime 0.2.6.87 GitHub first-run onboarding repair — 2026-09-21

- Audited the real first-run GitHub setup flow after a fresh end-user installation exposed that the UI could report a confusing/false authentication state and did not clearly tell the user how to advance.
- Removed the misleading “Open GitHub Login” action from the Setup workflow: browser login alone does not authorize the local Bridge and therefore must not be presented as a Bridge-connection step.
- GitHub CLI detection now inspects the credential stored by `gh auth login` with inherited `GH_TOKEN/GITHUB_TOKEN` removed from the CLI subprocess environment, preventing stale process tokens from masquerading as a valid CLI login.
- Local credential resolution now validates candidates against GitHub and can skip a stale/invalid candidate in favor of a valid dedicated Bridge token, stored GitHub CLI credential or later environment candidate.
- Added an explicit credential-validation endpoint and a guided Setup state machine: **authorize -> verify identity -> create/connect Bus**. One-click provisioning is disabled until a GitHub identity has been verified and the UI displays the verified login plus the exact next action.
- GitHub CLI web authorization is automatically re-polled after launch; Fine-grained Token users can paste and validate the token before any repository mutation.
- The verified GitHub login is used to auto-fill `<login>/ai-bridge-bus`, while the existing manual-repository flow remains available as an advanced fallback.
- Initial PR validation exposed two brittle legacy UI assertions (**511 PASS / 2 FAIL**); no product rollback was required. The assertions were changed to semantic checks and the user-visible “does not copy existing projects” guarantee was retained.
- Final PR #18 validation Run `35575348046` passed with all product gates.
- Main Run `35575490089` passed end-to-end: **513/513 pytest PASS**, release verifier PASS, generated artifact publication PASS, fresh GitHub install PASS and bundled offline dependency bootstrap PASS.
- Published Runtime bundle SHA-256: `810e185dd8bf9647c57e0df8a119c930e592a6a471200666aa461e31cf9f64e7`; generated artifact commit: `f9b2565218ec8c35ad32e2460f3977ed881c31f6`.


### Repository semantic-debt cleanup / Runtime 0.2.6.86 — 2026-09-21

- Audited the public product repository for duplicated authority, stale release semantics and dead migration artifacts instead of treating test success as proof that repository meaning was coherent.
- Removed obsolete Supervisor source payloads 0.1.2–0.1.5; the product tree now keeps only the Supervisor version referenced by the current release manifest. Installed rollback remains a local Runtime/Supervisor concern rather than a reason to retain duplicate historical source payloads in the public product tree.
- Removed 38 dead/obsolete artifacts in the first cleanup pass, including Runtime-bundled historical “authoritative” documents that still described Runtime 0.1.9.2 / Mailbox V3 / bridge-runtime, an unused Supabase-primary monkeypatch that hard-coded bridge_version 0.2.6.56, unused Bus templates, stale migration/release status documents and redundant .gitkeep files.
- Consolidated current authority to README -> normative specs -> docs/SUPABASE_PRIMARY_TRANSPORT.md. The legacy SUPABASE_FALLBACK_TRANSPORT.md now documents compatibility names only and no longer duplicates current routing/security/schema/setup instructions.
- Corrected current-product semantic drift: Unreal documentation now matches the ready AIBridgeUE plugin, Supabase UI labels primary transport as primary rather than fallback/backup, the SQL table comment matches current roles, and the public execution specification now models Supabase-primary/GitHub-V5-fallback routing.
- Removed stale UI cache-version literals (0.2.6.14 / 0.2.6.55 / 0.2.6.84) because the routes already enforce no-store/no-cache. Dashboard recent-command display is consistently five entries, and the displayed product update branch fallback is main rather than bridge-runtime.
- Added repository-hygiene regression tests so duplicate Supervisor payloads, parallel historical authority docs, the dead Supabase primary extension, hard-coded web cache versions and Unreal scaffold-status drift fail CI if reintroduced.
- Cleanup branch audit reduced the product tree to **326 blobs with zero duplicate-blob groups**; `bootstrap/supervisor` retains only **0.1.6**, and Runtime no longer ships a second historical authority-doc tree or the unused `supabase_primary_extension.py`.
- Initial PR validation exposed four tests that still hard-coded the removed Supervisor 0.1.5 path (**506 PASS / 4 FAIL**); those stale test references were corrected rather than restoring the obsolete source tree.
- Final PR #17 validation Run `35570768742` passed: deterministic build PASS, compileall PASS, **510/510 pytest PASS**, release verifier PASS and PowerShell parser PASS.
- Main validation Run `35571382741` passed end-to-end: **510/510 pytest PASS**, release verifier PASS, generated artifact publication PASS, fresh GitHub install PASS and bundled offline dependency bootstrap PASS.
- Published Runtime bundle SHA-256: `ffcafe44ca1243322ccde9a835c8292f500a27a11bd15ad1fd5fb9f00cc9f07d`; installer bundle SHA-256 remains `900b95279341b726295fb3a121b31d971157b395d7af0558f575d280df668697`; generated artifact commit: `354a40e128b9e59ac351ab0bb6ee51e0a4696895`.
- This was a public-product cleanup only. The private development-source baseline remains `dc9f4ef1ac7111ee86ca6cfaef95f512c4112a8c` and was not advanced.


### Runtime 0.2.6.85 / Supervisor 0.1.6 offline Python bootstrap — 2026-09-21

- Fixed a real fresh-machine startup failure where installation completed but first launch ran `pip install -e Runtime[test]`; PEP 517 build isolation then required live PyPI access for `setuptools>=68`, so TLS/proxy-restricted machines failed before Runtime startup.
- Added a pinned Windows release dependency lock derived from the last successful public Windows validation environment and package a local wheelhouse for CPython 3.11, 3.12, 3.13 and 3.14 x64 inside `runtime_bundle.zip`.
- Supervisor/bootstrap now installs Runtime/test dependencies from the bundled wheelhouse using `--no-index --find-links` before any network fallback. Normal fresh startup therefore requires GitHub release access but not PyPI access.
- Removed the unnecessary editable Runtime install from first-start bootstrap; Runtime continues to execute from its source tree through the Supervisor-managed `PYTHONPATH`.
- Product CI fresh-install smoke now sets `PIP_NO_INDEX=1`, runs the installed `_System\\bootstrap.bat`, and verifies all Runtime dependency imports, so future releases cannot silently reintroduce a first-launch PyPI dependency.
- Corrected the prior public-installer regression assertion to follow the refactored Git-blob helper rather than a case-sensitive local PowerShell variable spelling.
- PR #16 validation Run `35567737329` passed: deterministic release build, compileall, **505/505 pytest PASS**, release verifier PASS, and PowerShell parser PASS.
- Main validation Run `35567851939` passed end-to-end: **505/505 pytest PASS**, release verifier PASS, generated artifact publication PASS, fresh GitHub installation PASS, and first-launch dependency bootstrap PASS with `PIP_NO_INDEX=1`.
- The fresh-install smoke installed **Runtime 0.2.6.85 / Supervisor 0.1.6** and then successfully installed the bundled wheelhouse entirely offline. Published Runtime bundle SHA-256: `fe2bbe08a7351f3f6d105f935e8c4570f776a897104b26ee45ad7813dabae415`; installer bundle SHA-256: `900b95279341b726295fb3a121b31d971157b395d7af0558f575d280df668697`.
- Generated release artifact commit: `863c42ac1fc8e5a14b45a0332a412813d21cd988`. Explicit end-user GitHub/Host acceptance remains a separate gate.


### Installer public-repository 403 fallback — 2026-09-21

- Fixed fresh/public installation on machines with a stale, invalid, or unrelated `AI_BRIDGE_PRODUCT_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`.
- Product downloads now try the configured authenticated GitHub API path first, then retry the public repository anonymously, then fall back to `raw.githubusercontent.com` for public distribution files.
- The existing Git Blobs fallback for >1 MB Runtime archives and all SHA-256 verification remain intact.

### Runtime 0.2.6.84 public synchronization + Unreal one-click install — 2026-09-20

- Reconciled the shared product Runtime with the live-verified development baseline **0.2.6.84** while preserving the standalone product boundary: public Runtime/update authority remains `fmb22333-dev/AI-Bridge:main`; a user's Bus remains transport/project authority and is never an implicit Runtime release source.
- Integrated the post-0.2.6.56 transport/runtime work, including bounded execution-lane isolation, structured V5 result delivery and lazy full-result recovery, Local Direct support, Workspace lane isolation, Supabase primary realtime routing, Presence version authority, clean Bus provisioning transport authority, Runtime watchdog hardening, and the Windows task-idle completion notification.
- Synchronized Houdini Adapter **0.5.27** and regenerated distributable Knowledge through the clean filter; candidate/deprecated/project-family recipes remain excluded from distributable execution authority.
- Promoted **AIBridgeUE 0.5.3** into the distributable Runtime. Host Plugins now exposes Unreal Engine as `ready` with project-scoped one-click install/update. Installation copies the bundled plugin into the selected Unreal project's `Plugins/AIBridgeUE`, removes plugin-local stale `Binaries/` and `Intermediate/`, and fails closed when the target Unreal Editor process is running. Bridge does not restart Unreal automatically.
- Updated the product release contract from Houdini-only to **Houdini + Unreal**. Blender remains scaffold-only.
- Rebased clean Bus provisioning and Setup so new installs retain the newer Supabase-primary/Presence discovery semantics while still pointing Runtime/protocol authority to the public product repository rather than the private development Bus.
- Upgraded the product Supervisor to **0.1.5**, retaining its transport watchdog and candidate-validation improvements while preserving the public fail-closed `BUS_RUNTIME_BOOTSTRAP_FORBIDDEN` rule and `AI-Bridge:main/runtime-release.json` default update source.
- Development-source interval from the prior public baseline `c9be86a58899e581621d30f82a1c5783d30a76f9` to the accepted 0.2.6.84 source mirror `dc9f4ef1ac7111ee86ca6cfaef95f512c4112a8c` was classified before synchronization. Project authority/history, live state, machine paths, credentials, command history and project-family evidence remain **DO NOT DISTRIBUTE**.
- Live developer validation before public synchronization: Runtime publication/activation **0.2.6.84**, full Runtime **486/486 PASS**, compile PASS, Knowledge publish gate PASS, Supervisor **0.1.5** running, Supabase `transport.ping` SUCCESS, and AIBridgeUE **0.5.3** reported by `bridge.plugin.status`.
- Public PR validation passed on Run `35512508367`: deterministic release build, compileall, **502/502 pytest PASS**, release verifier and PowerShell parser all passed before the Runtime synchronization merge.
- The first merged-main smoke exposed a GitHub Contents API size edge case after AIBridgeUE expanded `runtime_bundle.zip` beyond 1 MB: Contents metadata omits inline `content`. PR #14 changed the installer to fall back to the authenticated `git_url` / Git Blobs API path while preserving bounded retries and SHA-256 verification.
- Final main validation Run `35564303705` attempt 2 passed end-to-end: deterministic build PASS, compile PASS, **503/503 pytest PASS**, release verifier PASS, PowerShell parser PASS, generated artifact publication PASS, and fresh Windows GitHub install smoke PASS.
- Final published product: Runtime **0.2.6.84**, Houdini Adapter **0.5.27**, AIBridgeUE **0.5.3**, Supervisor **0.1.5**. Runtime bundle SHA-256: `1d47db1734d2b9911494e97adbee2a955f23e2dcc7ad24d6680049a7a02242f9`; installer bundle SHA-256: `51c200bfdd37add36f2a7a4b142fcbb3cc8c12a284aa7d1f0b2cd20fd6054911`; Knowledge digest: `cdb49df281c07e67b6290fe380ef1e704d1c19ca0ee3c0a129fde40ee1e91841`.
- Generated release artifact commit: `f734b9220c3b5f2b45432480dcc9def245513d11`. Migration baseline advanced from `c9be86a58899e581621d30f82a1c5783d30a76f9` to development source mirror `dc9f4ef1ac7111ee86ca6cfaef95f512c4112a8c`. Explicit real end-user account/Host acceptance remains separately pending.

### Runtime 0.2.6.55 Supabase Backup Bus UI — 2026-09-17

- Promoted Supabase fallback configuration from headless environment bootstrap to a first-class **Supabase Backup Bus** in AI Bridge Setup and Dashboard while keeping GitHub Bus as primary transport.
- Setup now accepts Project URL, password-masked Secret Key and poll interval, automatically inherits the connected GitHub Bus Bridge ID, performs a live Data API health test before persistence, and allows a blank Secret Key on later edits to reuse the DPAPI-saved credential.
- Dashboard now exposes backup transport status plus Modify, Test Connection, and Disconnect & Clear Credential actions. Disconnect removes both `fallback_transport.json` and the locally encrypted Supabase credential without touching GitHub or the Supabase project.
- Secret material is never returned by status/test APIs and remains stored through the existing SecretStore / Windows DPAPI path. Environment variables remain supported only for headless/recovery bootstrap.
- Isolated the UI/API integration in `fallback_routes.py`, `fallback_setup.js`, and `fallback_dashboard.js` instead of expanding the existing monolithic web modules.
- TDD RED gate: all **117 pre-existing tests passed** while the four new lifecycle/UI contract tests failed only for the intentionally missing feature surface. GREEN gate: public Windows product validation passed deterministic release build, compileall, full pytest, release verifier and PowerShell parsing; latest validated PR run before closeout was Run #69 (`35193802549`).
- The developer live Runtime was already newer than the public product, so it was not downgraded. This feature was forward-ported onto live Runtime `0.2.6.75` as `0.2.6.76`; targeted Supabase regression was **5/5 PASS**, full live validation was **470/470 PASS**, Knowledge publish gate passed, and Supervisor activated `0.2.6.76` with rollback available and no update error.
- Runtime public product target is `0.2.6.55`; Houdini Adapter and distributable Knowledge authority are unchanged by this UI/lifecycle delta.

### Runtime 0.2.6.54 independent Supabase fallback transport — 2026-09-17

- Added an optional Supabase/PostgREST secondary command transport while keeping GitHub V5 Issue Comment + Contents as the canonical primary transport and product/project authority path.
- The Supabase runner is independent and polls in parallel with GitHub instead of waiting for a GitHub outage. This specifically covers AI-client GitHub write-tool failure while GitHub reads and the local Bridge remain healthy.
- Both transports preserve the same canonical `bridge/1` `CommandEnvelope` and `command_id`; BridgeDB/TransportRunner remain the execution authority, so cross-transport observations converge on one durable command identity/result rather than authorizing a second host-side mutation.
- Supabase rows use durable `queued -> accepted -> terminal` state transitions; malformed envelopes are marked `rejected`, accepted rows are re-polled after Runtime restart, and terminal receipt publication remains retryable without host re-execution.
- Added backend-key handling for current `sb_secret_*` keys via `apikey` only and compatibility handling for legacy JWT-style service-role keys; `sb_publishable_*` keys are rejected for the Bridge backend path.
- Added a separate `SupabaseFallbackController`; fallback configuration/credential failures do not stop the GitHub runner. Non-secret fallback settings persist to `fallback_transport.json`; the backend secret uses the existing SecretStore/Windows DPAPI path.
- GitHub Presence now advertises `fallback_transport` state so an authorized AI can discover whether Supabase failover is actually connected before attempting it.
- Added `docs/SUPABASE_FALLBACK_TRANSPORT.md`, `docs/supabase_fallback_schema.sql`, and failover rules to `specs/AI_AGENT_PROTOCOL.md`. GitHub remains the bootstrap authority; Supabase is an optional emergency ingress/result path only.
- Added regression coverage for Supabase queue/ACK/result round-trip, malformed-envelope rejection, current secret-key header behavior, legacy service-role compatibility, and publishable-key rejection. Isolated targeted validation: **4/4 PASS** before publication; full public Windows product validation is required before merge.
- Runtime version target is `0.2.6.54`; Houdini Adapter remains `0.5.26`; Supervisor remains `0.1.4`; distributable Knowledge authority is unchanged by this Runtime-only transport delta.
- Live Supabase activation remains an external deployment step: this change ships transport/schema/configuration support but does not create a user Supabase project or store any project credential in GitHub.

### Runtime 0.2.6.53 fallback bootstrap persistence — 2026-09-12

- Persisted the GitHub Contents command-index baseline in the Runtime database so a Runtime restart no longer forgets which fallback command files were already present.
- In V5 hybrid Comment + Contents mode, the first poll without a persisted Contents baseline now seeds the current file index without executing historical files; later polls execute only genuine SHA changes/new files.
- `TransportRunner` restores the persisted Contents index before polling and persists the refreshed snapshot immediately after ingress collection.
- Added public regression coverage for first-poll baseline seeding, restored-index delta detection, database round-trip, Runtime restart recovery, and existing multi-ingress conflict/merge semantics.
- Rebased public Dashboard/command-history cache keys to `0.2.6.53` while intentionally rejecting the development-source `0.2.6.14` static-cache regression; public product UI behavior otherwise remains unchanged.
- Houdini Adapter remains `0.5.26`; Supervisor remains `0.1.4`; distributable Knowledge authority/digest is unchanged because the accepted source Adapter/Knowledge trees did not change.
- Development source target: `<private-development-source>@c9be86a58899e581621d30f82a1c5783d30a76f9`; private Runtime pre-publish validation was **348/348 PASS**.
- Public PR #8 validation run `34661225082` passed deterministic release build, `compileall`, full product tests, release verifier, and PowerShell parser.
- PR #8 merged to `main` at `10b9ab650d3b3457a93c664ac7b463954ad5e0d0` on `2026-09-12T00:21:07Z`.
- Final `main` product validation run `34661336612` passed, including generated release artifact publication and fresh Windows installation from the public GitHub branch.
- Generated artifact commit: `3943a716378166964f2af93bedb1e7ae2b0f8617`; published Runtime release source commit: `10b9ab650d3b3457a93c664ac7b463954ad5e0d0`.
- Published Runtime bundle SHA-256: `76654d312641b0037b981cc063012300a1e48e22de48e29550241845c482caa5`; installer bundle remains `4db513b7976c1eccd9e08418454a6404f267896ee33f1fea81a6fa3121de1a5c`; clean Knowledge digest remains `f2fabe69cad95c9722a285c2fa5f91f91837a75d9dda73a8d236d53351fcf093`.
- Migration baseline advanced from `1ea396efd020525ec6fe6c817c9cb2f58c027d58` to `c9be86a58899e581621d30f82a1c5783d30a76f9`. Runtime 0.2.6.53 public source/release synchronization is complete; explicit real-environment end-to-end acceptance remains separately pending.

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
- Development source baseline: `<private-development-source>@c9be86a58899e581621d30f82a1c5783d30a76f9`.
- Runtime 0.2.6.53 public source/release synchronization is complete: PR validation, `main` artifact publication, release verification, and fresh Windows GitHub install smoke all passed. The active developer Runtime and public product are aligned at 0.2.6.53; explicit real-environment end-to-end acceptance remains separately pending.

### Safety
- Public/distributable content excludes developer Bridge IDs, credentials, sessions, workspaces, command history, project authority documents, local paths, and project-family/candidate knowledge as generic execution authority.
- Repository migration remains isolated from the developer's active Houdini sessions and live Runtime.
- Legacy Supervisor behavior that bootstraps a `bridge-runtime` branch inside a user's Bus is forbidden.
- Productized files are not blindly overwritten during Delta Sync; architecture-sensitive changes require reconciliation.

## 2026-09-09 — Repository bootstrap
- Created the dedicated `fmb22333-dev/AI-Bridge` product repository.
- Established the two-repository product model: shared product repository + per-install user Bus.
- Began source migration without switching or modifying the developer's live Bridge Runtime.
