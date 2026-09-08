# AI Bridge Update / Synchronization Workflow

This document defines the mandatory repository workflow for Bridge Runtime, adapters, installer, protocol, and distributable Knowledge Pack changes.

## Core rule

`fmb22333-dev/AI-Bridge` is the shared product repository. A Bridge or generic Knowledge Pack update is not complete until the corresponding product changes are synchronized here.

The developer Bus remains a live execution/state bus and must not become the long-term product source of truth.

## Before any product update

Before modifying Bridge Runtime, adapters, installer, protocol, or generic knowledge:

1. Read the current repository head and recent commits for `fmb22333-dev/AI-Bridge`.
2. Inspect open/recent pull requests when collaboration is active.
3. Determine whether another contributor changed files relevant to the planned update.
4. Reconcile those changes before writing; never blindly overwrite a newer remote version.
5. If ownership or semantics conflict, preserve both changes and resolve explicitly rather than force-replacing `main`.

For AI agents, this preflight is mandatory even when the agent believes it already knows the repository state from chat history.

## During development

- Work from the latest observed repository state.
- Keep generic product knowledge separate from project-specific evidence.
- Do not copy `.ai-bridge` command/result history, credentials, Bridge IDs, local paths, live sessions, workspaces, or project authority documents into the product repository.
- Runtime/Adapter kernel changes and Knowledge Pack changes are separate concerns; normal knowledge growth should remain hot-loadable when the architecture supports it.
- Preserve deterministic clean-distribution filtering.

## Knowledge promotion and synchronization

A reusable discovery follows this path:

`observed -> candidate -> validated -> promoted -> clean distribution`

Only distributable generic knowledge that passes the Learning Policy and clean-distribution filter belongs in the public/shared Knowledge Pack.

When promoted generic knowledge changes:

1. update the canonical machine-readable knowledge source;
2. update tests/evidence required for promotion;
3. regenerate/validate the clean Knowledge Pack;
4. synchronize the resulting source/generator changes to `AI-Bridge`;
5. update release/version metadata when the shipped product changes.

Project-family candidates and historical evidence may remain in development evidence stores but must not silently enter generic distribution authority.

## After an update

Before considering a Bridge/Knowledge update complete:

1. verify the repository head again;
2. confirm no conflicting remote change landed during the work;
3. run the relevant regression/validation suite when an executable environment is available;
4. update product/migration/release state documentation as needed;
5. publish/synchronize the accepted source to `fmb22333-dev/AI-Bridge`;
6. only then build or publish a distributable installer/runtime artifact.

## Generated user Bus repositories

A user's per-install Bus is not synchronized by copying this product repository into it.

The Bus contains its own:

- `PROJECT_STATE_INDEX.json`
- `AI_BRIDGE_READ_FIRST.md`
- root `README.md` AI entry hint
- runtime presence/status
- transport resources
- user project authority documents

The index points back to this shared product repository for Runtime/protocol/knowledge authority.

## AI collaboration rule

Before an AI changes this repository, it must assume that another human or AI may have contributed since its last turn. Therefore:

`remote preflight -> reconcile -> modify -> validate -> remote recheck -> publish`

This rule exists to prevent stale-chat state from overwriting newer GitHub state.
