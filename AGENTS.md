# AI Agent Entry Rules

## Repository role

This repository is the shared AI Bridge product repository, not a live per-user Bus.

## Mandatory rules

1. Do not treat this repository as authority for a user's live runtime state.
2. Never copy developer-machine state, credentials, Bridge IDs, sessions, workspaces, local paths, command history, or project authority documents into distribution artifacts.
3. Keep generic runtime/adapter knowledge separate from project-specific evidence.
4. Any generated per-install Bus must use `PROJECT_STATE_INDEX.json` as its single machine entrypoint.
5. In a user Bus, follow the authority order declared by that index and prefer indexed pointers over repository-wide discovery.
6. Never guess project/session/workspace/host targets when ambiguity exists.
7. Reusable knowledge becomes distributable only after validation/promotion under the Learning Policy.
8. Distribution builds must be deterministic and must not mutate a live local Bridge or host application session.

## Safety boundary

Building or editing this repository must not require restarting Houdini, replacing the active Adapter, switching the live Runtime, or modifying any current HIP file unless an explicit runtime-upgrade task requires it.
