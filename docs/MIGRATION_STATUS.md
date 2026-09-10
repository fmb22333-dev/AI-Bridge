# AI Bridge Standalone Product Status

**Date:** 2026-09-10  
**Product repository:** `fmb22333-dev/AI-Bridge`  
**Development source baseline:** `<private-development-source>@42b2492565a5bb3b15e0cd2df3cb216ecae297ab`

## Current state

The Runtime 0.2.6.50 standalone product migration is **complete at code, release assembly, and clean Windows installation-smoke level**.

Current product versions:

- Runtime: **0.2.6.50**
- Houdini Adapter: **0.5.26**
- Supervisor: **0.1.4**

The only remaining gate is explicit real-environment end-to-end acceptance. No implementation/migration blocker remains known.

## Product authority split

### Shared product repository

`fmb22333-dev/AI-Bridge:main` is authoritative for:

- Runtime
- Supervisor
- host adapters
- installer/setup
- generic promoted Knowledge
- protocol/specifications
- release manifests and bundles

### Per-install user Bus

A user Bus is authoritative for:

- `PROJECT_STATE_INDEX.json`
- `AI_BRIDGE_READ_FIRST.md`
- Bridge presence/status
- V5 command/result transport
- user project authority

A user Bus is **never** an implicit Runtime release source. Product update/bootstrap paths fail closed against the legacy Bus-as-release-source model.

## Completed implementation

- Runtime/Adapter delta reconciled through development source commit `42b2492565a5bb3b15e0cd2df3cb216ecae297ab`.
- BridgeAdmin product authority and user Bus authority separated.
- Full Setup backend rebased to shared product update authority.
- Dashboard static assets completed.
- Supervisor 0.1.4 integrated with mature monitoring/activation/rollback while removing legacy Bus Runtime publication/bootstrap.
- Cross-platform clean Knowledge digest made canonical.
- Clean registry dangling project-family recipe authority fixed.
- Product release builder and verifier added.
- Deterministic Runtime and installer ZIPs added.
- One-click PowerShell/BAT installer added with SHA-256 verification.
- Installer GitHub access handles private repository credentials through process/user auth sources without committing credentials.
- GitHub installer reads use bounded retry.
- Product CI serializes release assembly per branch to avoid artifact race.

## Release artifacts

- `runtime_bundle.zip`
- `runtime-release.json`
- `supervisor-release.json`
- `release-manifest.json`
- `INSTALL_AI_BRIDGE.ps1`
- `INSTALL_AI_BRIDGE.bat`
- `AI_Bridge_Installer.zip`
- `knowledge/houdini/distribution_manifest.json`

Validated hashes:

- Runtime bundle: `00ee9abd2b5533667c86256b464725d7c284d5841e2a8cb8b57ad5f4f19a822e`
- Installer bundle: `4db513b7976c1eccd9e08418454a6404f267896ee33f1fea81a6fa3121de1a5c`
- Clean Knowledge: `f2fabe69cad95c9722a285c2fa5f91f91837a75d9dda73a8d236d53351fcf093`

## Clean Knowledge boundary

Distributable promoted recipes:

- `code.safe_patch_and_cook`
- `cook.checked`
- `fbx.character_import.set_animation_and_cook`
- `kinefx.import_with_frameinfo`
- `network.ensure_and_cook`
- `parm.safe_write_and_cook`

Not distributed as generic execution authority:

- `<project-family-recipe>` — project-family
- `network.build` — candidate
- `network.build_and_cook` — deprecated

The clean registry and recipe set are internally consistent; the prior dangling project-family authority defect is fixed.

## Validation evidence

Windows product validation run `34425959944`:

- release build: **PASS**
- `compileall`: **PASS**
- full product tests: **84/84 PASS**
- release verifier: **PASS**
- PowerShell parser: **PASS**
- generated release artifact publication: **PASS**
- fresh Windows-directory install by downloading from GitHub: **PASS**

The smoke installation produced Runtime 0.2.6.50 + Supervisor 0.1.4 in a clean target directory and verified standalone product update authority.

## Migration baseline

`migration/BASELINE.json` is advanced to development source commit:

`42b2492565a5bb3b15e0cd2df3cb216ecae297ab`

Future synchronization must compare from this point and retain the classification:

- **SAFE COPY** — ordinary generic Runtime/Adapter fixes.
- **REBASE REQUIRED** — Supervisor/update source, Setup/provisioning, installer/release authority, BridgeAdmin, product authority files.
- **KNOWLEDGE REGENERATE** — regenerate/filter from source Knowledge; never raw-copy.
- **DO NOT DISTRIBUTE** — runtime state/history, credentials, machine IDs, local paths, project authority/handoffs, project-family/candidate Knowledge.

## Remaining gate: explicit real-environment acceptance

Not executed in this completion:

1. Create a genuinely new user GitHub Bus through the Setup UI.
2. Complete interactive GitHub authorization in that end-user flow.
3. Connect the new Bus to the clean installed Runtime.
4. Install/activate the Houdini Adapter in that fresh installation.
5. Verify a live Houdini command/result round-trip.

This is an **acceptance gate only**, not a known implementation blocker.

## Live developer isolation

This migration/release assembly did not intentionally switch the developer's live Runtime update authority, restart Houdini, mutate a HIP, or rewrite the existing development Bus. Live activation should remain a separate explicit decision after acceptance.
