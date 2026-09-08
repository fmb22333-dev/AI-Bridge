# AI Agent Entry Rules

## Repository role

This repository is the shared AI Bridge product repository, not a live per-user Bus.

## Mandatory rules

1. Do not treat this repository as authority for a user's live runtime state.
2. Before changing Bridge Runtime, adapters, installer, protocol, or generic knowledge, inspect the current repository head/recent commits and relevant open/recent PRs. Never rely on stale chat state when GitHub can be newer.
3. Reconcile newer remote changes before writing. Do not blindly overwrite another contributor's work.
4. Read `CHANGELOG.md` before material product work. If the accepted change affects product behavior, safety, compatibility, protocol, installer/update behavior, public capabilities, or distributable knowledge authority, update `CHANGELOG.md` in the same accepted change set.
5. During migration from a continuously changing development source, read `migration/BASELINE.json` before source synchronization. Compare from the recorded source commit/tree; do not rediscover the entire source repository on every sync.
6. Classify source deltas before applying them: `safe_copy`, `rebase_required`, or `do_not_distribute`. Productized files must never be blindly overwritten by development-source copies.
7. Advance `migration/BASELINE.json` only after the delta has been reconciled, validated, synchronized to this repository, and recorded in `CHANGELOG.md`.
8. Knowledge synchronization is regeneration, not blind copying: build the distributable Knowledge Pack from the latest source Knowledge tree using the clean filter; candidate/project-family evidence is not generic execution authority.
9. Never copy developer-machine state, credentials, Bridge IDs, sessions, workspaces, local paths, command history, or project authority documents into distribution artifacts.
10. Keep generic runtime/adapter knowledge separate from project-specific evidence.
11. Any generated per-install Bus must use `PROJECT_STATE_INDEX.json` as its single machine entrypoint.
12. In a user Bus, follow the authority order declared by that index and prefer indexed pointers over repository-wide discovery.
13. Never guess project/session/workspace/host targets when ambiguity exists.
14. Reusable knowledge becomes distributable only after validation/promotion under the Learning Policy.
15. Distribution builds must be deterministic and must not mutate a live local Bridge or host application session.
16. A Bridge or promoted generic Knowledge Pack update is not complete until accepted source/generator changes and required changelog entries are synchronized here and remote state is rechecked for conflicts.

## Required update sequence

`read changelog -> read migration baseline when applicable -> remote preflight -> compute/classify delta -> reconcile -> modify -> validate -> update changelog -> remote recheck -> synchronize/publish -> advance baseline only after success`

See:
- `docs/UPDATE_WORKFLOW.md` — synchronization workflow
- `docs/CHANGE_POLICY.md` — mandatory audit/change-log policy
- `docs/DELTA_SYNC_WORKFLOW.md` — continuous development-source synchronization
- `migration/BASELINE.json` — current machine-readable migration anchor

## Safety boundary

Building or editing this repository must not require restarting Houdini, replacing the active Adapter, switching the live Runtime, or modifying any current HIP file unless an explicit runtime-upgrade task requires it.
