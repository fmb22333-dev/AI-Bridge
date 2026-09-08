# AI Agent Entry Rules

## Repository role

This repository is the shared AI Bridge product repository, not a live per-user Bus.

## Mandatory rules

1. Do not treat this repository as authority for a user's live runtime state.
2. Before changing Bridge Runtime, adapters, installer, protocol, or generic knowledge, inspect the current repository head/recent commits and relevant open/recent PRs. Never rely on stale chat state when GitHub can be newer.
3. Reconcile newer remote changes before writing. Do not blindly overwrite another contributor's work.
4. Read `CHANGELOG.md` before material product work. If the accepted change affects product behavior, safety, compatibility, protocol, installer/update behavior, public capabilities, or distributable knowledge authority, update `CHANGELOG.md` in the same accepted change set.
5. Never copy developer-machine state, credentials, Bridge IDs, sessions, workspaces, local paths, command history, or project authority documents into distribution artifacts.
6. Keep generic runtime/adapter knowledge separate from project-specific evidence.
7. Any generated per-install Bus must use `PROJECT_STATE_INDEX.json` as its single machine entrypoint.
8. In a user Bus, follow the authority order declared by that index and prefer indexed pointers over repository-wide discovery.
9. Never guess project/session/workspace/host targets when ambiguity exists.
10. Reusable knowledge becomes distributable only after validation/promotion under the Learning Policy.
11. Distribution builds must be deterministic and must not mutate a live local Bridge or host application session.
12. A Bridge or promoted generic Knowledge Pack update is not complete until accepted source/generator changes and required changelog entries are synchronized here and remote state is rechecked for conflicts.

## Required update sequence

`read changelog -> remote preflight -> reconcile -> modify -> validate -> update changelog -> remote recheck -> synchronize/publish`

See:
- `docs/UPDATE_WORKFLOW.md` — synchronization workflow
- `docs/CHANGE_POLICY.md` — mandatory audit/change-log policy

## Safety boundary

Building or editing this repository must not require restarting Houdini, replacing the active Adapter, switching the live Runtime, or modifying any current HIP file unless an explicit runtime-upgrade task requires it.
