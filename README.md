# AI Bridge

AI Bridge is a distributable local bridge/runtime for AI-assisted control of host applications such as Houdini, Unreal Engine, and Blender.

## Repository role

This repository is the shared **Runtime / Adapter / Installer / Generic Knowledge** source.

It is intentionally separate from each user's per-install **Bus repository**.

A user installation should create its own Bus repository containing runtime presence, transport resources, project authority documents, and the machine entrypoint `PROJECT_STATE_INDEX.json`.

## Distribution boundary

This repository may contain:

- Bridge Core and Supervisor
- Host adapters
- Installer/bootstrap assets
- Protocol and normative specifications
- Clean generic Knowledge Pack
- Clean Bus/project templates
- Release manifests and distributable bundles

It must not contain:

- developer-machine Bridge IDs
- GitHub tokens or secret material
- current workspaces/sessions/PIDs
- command or recovery history
- local install paths
- active project authority documents
- project-specific HIP/FBX paths
- private project-family execution recipes

## AI integration

AI agents working on this repository should read `AGENTS.md` first.

AI agents working on a generated user Bus repository must read that Bus repository's `PROJECT_STATE_INDEX.json` before any project or Bridge operation.

## Status

Repository bootstrap initialized. Runtime migration and public-distribution validation are the next steps.
