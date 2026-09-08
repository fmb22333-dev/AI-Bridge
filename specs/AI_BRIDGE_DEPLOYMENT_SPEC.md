# AI Bridge — Clean Deployment Specification

**Status:** Public deployment contract

## Product definition

A distributable AI Bridge package is a **clean skeleton with inherited generic knowledge**. It is not a clone of the developer machine, developer Bus repository, or any active Houdini/Unreal/Blender project.

## Repository model

### Shared product repository

`fmb22333-dev/AI-Bridge`

Provides Runtime, Supervisor, host adapters, installer/bootstrap assets, generic distributable Knowledge Pack, protocol/specifications, clean templates, and release metadata.

### Per-install Bus repository

Created fresh during first-run setup. It contains only the installation's machine entrypoint, live status, transport resources, and project authority documents created by that installation.

It must not contain a copied development repository or another user's project history.

## Local package boundary

The installable package may contain only immutable/bootstrap material:

- installer/bootstrap launcher
- Supervisor
- stable Runtime bundle
- host plugin payloads
- distributable generic Knowledge Pack
- clean Bus templates
- first-run Setup UI

The following are generated on the target machine during first run:

- local data directory
- Bridge ID
- local credential/secret storage
- Bus repository configuration
- Workspace registry
- runtime status
- host-plugin installation state

## Never distribute

Do not ship or commit:

- GitHub credentials/tokens
- developer Bridge ID
- developer Bus repository state
- current Workspaces/Sessions/PIDs
- command/result/recovery history
- local install paths
- HIP/FBX/project paths
- active project authority documents
- candidate or project-family knowledge as generic execution authority

## First-run flow

`download/extract -> run installer -> Setup UI -> authorize GitHub -> Create & Connect Bus -> install selected host adapters -> ready`

The user must not be required to manually create JSON files, transport issues/comments, status folders, or project index records.

## GitHub provisioning contract

First-run provisioning must be idempotent and able to create or safely repair a dedicated Bus repository.

Required result:

1. create or select a clean dedicated repository;
2. resolve its default branch;
3. write a clean `PROJECT_STATE_INDEX.json`;
4. write `AI_BRIDGE_READ_FIRST.md`;
5. initialize required GitHub transport resources;
6. publish initial Bridge presence/status;
7. verify required read/write permissions;
8. save Bus configuration locally;
9. keep Runtime update/product source pointed at `fmb22333-dev/AI-Bridge` independently of the user's Bus.

No user project is created until explicitly registered.

## AI onboarding contract

The generated Bus repository must be self-describing to an authorized AI.

An AI only needs:

- access to the Bus repository; and
- the rule `read PROJECT_STATE_INDEX.json first`.

The index then points to live state, project authority, Runtime source, and normative protocol/specifications.

## Knowledge distribution contract

The public Knowledge Pack contains only generic validated/promoted operational knowledge. Project-family evidence may be retained in private development history, but must be filtered out of public distribution artifacts.

Normal Knowledge Pack updates should be hot-loadable where supported and must fail closed if validation fails.

## Runtime isolation

Building or publishing this repository must not mutate a developer's currently active local Bridge, host sessions, or project files. Runtime activation/replacement is a separate explicit operation.

## Acceptance criteria

A clean deployment passes only when:

- a machine with no prior AI Bridge data can initialize from the package;
- GitHub Bus resources can be created from Setup UI;
- the resulting Bus contains no previous user's project state;
- Runtime product source and Bus repository remain separate;
- a second installation receives independent Bridge identity/state;
- a newly authorized AI can recover the installation from `PROJECT_STATE_INDEX.json` without relying on prior chat context.
