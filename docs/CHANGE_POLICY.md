# Change Logging / Audit Policy

## Purpose

AI Bridge is expected to be modified by multiple humans and multiple AI agents. Git commits preserve raw history, but raw commit history alone is not sufficient for product audit, release decisions, or deciding whether another installation should update.

Therefore `CHANGELOG.md` is the canonical human-readable product change log.

## What must be logged

Add a concise entry under `CHANGELOG.md -> Unreleased` when a change materially affects any of the following:

- Runtime behavior or version;
- Supervisor/bootstrap behavior;
- host adapters or supported host versions;
- installer / first-run / GitHub Bus provisioning;
- AI protocol, transport, state recovery, or repository authority;
- safety, checkpoint, rollback, timeout, conflict, or recovery semantics;
- distributable Knowledge Pack content or promotion authority;
- public API / capability / recipe surface;
- user-visible UI or operational workflow;
- compatibility requirements or external prerequisites;
- release artifacts, hashes, manifests, or update authority;
- important fixes whose absence could cause wrong execution, data loss, hangs, or recovery failure.

## What normally does not need a changelog entry

Do not create noise for:

- spelling-only edits;
- formatting-only edits;
- comments with no behavioral implication;
- temporary experiments that never become accepted source;
- test-only refactors that do not change the product contract;
- migration mechanics that are already fully represented by an existing changelog item.

If a minor-looking change alters AI behavior, safety, compatibility, or update decisions, it is not minor and must be logged.

## Entry categories

Prefer these headings where useful:

- `Added`
- `Changed`
- `Fixed`
- `Safety`
- `Knowledge`
- `Deprecated`
- `Removed`
- `Compatibility`
- `Pending`

Entries should explain **what changed and why it matters**, not merely repeat a filename or commit message.

## Mandatory update workflow

For Bridge Runtime / Adapter / Installer / Protocol / Knowledge work:

1. read current `CHANGELOG.md`;
2. perform remote preflight according to `docs/UPDATE_WORKFLOW.md`;
3. reconcile relevant remote changes before editing;
4. implement and validate the change;
5. update `CHANGELOG.md` in the same accepted change set when the logging criteria are met;
6. recheck remote state before publishing;
7. synchronize accepted source, tests, protocol, and changelog together.

A meaningful product change is not considered fully synchronized if its changelog entry is missing.

## Knowledge-specific audit rule

For promoted generic knowledge, log at least:

- capability/recipe/rule family added or materially changed;
- promotion/demotion when execution authority changes;
- compatibility scope when relevant;
- validation status significant enough to affect distribution/update decisions.

Candidate/project-family evidence does not need to appear in the public product changelog until it affects shared product behavior or promotion authority.

## Releases

When a release is cut:

1. move accepted `Unreleased` entries into a version/date section;
2. leave a fresh `Unreleased` section at the top;
3. record release artifact/manifests when they are operationally relevant;
4. keep detailed validation evidence in release/migration documents rather than bloating the changelog.

## Authority boundaries

- `CHANGELOG.md` answers: **what changed in the product and why should I care?**
- Git history answers: **exactly which source edits/commits occurred?**
- release manifests answer: **which artifact/version is shipped?**
- `PROJECT_STATE_INDEX.json` in each Bus answers: **what is the authoritative live/project state and where should AI read next?**

The changelog must never replace live Runtime authority or project-state authority.
