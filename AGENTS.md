# AI Agent Entry Rules

## Repository role

This repository is the shared AI Bridge product repository, not a live per-user Bus.

## Mandatory rules

1. Do not treat this repository as authority for a user's live runtime state.
2. Before changing Bridge Runtime, adapters, installer, protocol, or generic knowledge, inspect the current repository head/recent commits and relevant open/recent PRs. Never rely on stale chat state when GitHub can be newer.
3. Reconcile newer remote changes before writing. Do not blindly overwrite another contributor's work.
4. Never copy developer-machine state, credentials, Bridge IDs, sessions, workspaces, local paths, command history, or project authority documents into distribution artifacts.
5. Keep generic runtime/adapter knowledge separate from project-specific evidence.
6. Any generated per-install Bus must use `PROJECT_STATE_INDEX.json` as its single machine entrypoint.
7. In a user Bus, follow the authority order declared by that index and prefer indexed pointers over repository-wide discovery.
8. Never guess project/session/workspace/host targets when ambiguity exists.
9. Reusable knowledge becomes distributable only after validation/promotion under the Learning Policy.
10. Distribution builds must be deterministic and must not mutate a live local Bridge or host application session.
11. A Bridge or promoted generic Knowledge Pack update is not complete until the accepted source/generator changes are synchronized to this repository and remote state is rechecked for conflicts.

## Required update sequence

`remote preflight -> reconcile -> modify -> validate -> remote recheck -> synchronize/publish`

See `docs/UPDATE_WORKFLOW.md` for the full synchronization contract.

## Safety boundary

Building or editing this repository must not require restarting Houdini, replacing the active Adapter, switching the live Runtime, or modifying any current HIP file unless an explicit runtime-upgrade task requires it.
