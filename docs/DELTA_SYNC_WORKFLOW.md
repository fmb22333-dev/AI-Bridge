# Continuous Delta Synchronization

AI Bridge is migrated from a development source that remains active while the product repository is being initialized. Migration therefore uses a persistent baseline instead of assuming the source is frozen.

## Canonical baseline

Read `migration/BASELINE.json` before synchronizing from the development source.

The baseline records at least:

- development source repository/ref/commit;
- Runtime source tree;
- Houdini Adapter tree/version;
- Knowledge tree;
- promotion registry blob;
- recipes tree;
- live Runtime version observed at capture time;
- product-repository head before the baseline record.

## Delta cycle

```text
read CHANGELOG
-> read migration/BASELINE.json
-> inspect current product-repo HEAD / PRs
-> resolve current development-source HEAD
-> compare baseline source commit -> current source commit
-> classify changed paths
-> reconcile/apply
-> regenerate clean Knowledge Pack from latest source knowledge
-> compile/test
-> update CHANGELOG
-> remote recheck
-> synchronize/publish
-> advance migration baseline
```

The baseline MUST NOT move merely because the source advanced. It moves only after the corresponding delta is reconciled and validated in this repository.

## Delta classes

### SAFE COPY

Normally eligible for direct source synchronization when the product path has not diverged:

- ordinary Runtime bug fixes;
- generic Adapter kernel fixes;
- generic regression tests;
- host scaffolds that contain no local/project state.

Direct copy still requires validation.

### REBASE REQUIRED

Must be understood and reconciled against the product architecture rather than overwritten:

- Supervisor/update-source logic;
- GitHub Bus provisioning and default product authority;
- Setup UI first-run repository initialization;
- installer/release source logic;
- generated Bus templates;
- any file intentionally changed in this repository to implement the shared-product + per-install-Bus model.

### DO NOT DISTRIBUTE

Never synchronize into the product repository or a clean installer:

- `.ai-bridge/` command/result/status history;
- credentials/secrets;
- developer Bridge IDs;
- developer sessions/workspaces/PIDs/local paths;
- AutoUV/Retarget/Subdiv/Locomotion authority or handoff files;
- project-family/candidate evidence presented as generic execution authority;
- caches, generated package metadata, local staging/recovery state.

## Knowledge synchronization

Knowledge is intentionally more dynamic than the Adapter kernel.

Do not treat the current clean output as a source tree to incrementally patch. After each reconciled source delta:

1. resolve the latest development Knowledge tree;
2. run the clean Knowledge Pack generator/filter;
3. include only generic validated/promoted distribution-authorized content;
4. exclude project-specific evidence and candidate-only execution authority;
5. validate deterministic output and regression tests;
6. replace the generated clean output atomically.

A changed Knowledge tree or promotion-registry blob is a meaningful delta even when the Runtime version and Adapter version do not change.

## Audit

Every baseline advancement must record in `CHANGELOG.md`:

- old source baseline commit;
- new reconciled source commit;
- Runtime/Adapter version movement when applicable;
- whether Knowledge tree/promotion authority changed;
- major REBASE decisions;
- validation status.

This gives later maintainers and AI agents a fast answer to: "What source state is already incorporated into the product repository?"
