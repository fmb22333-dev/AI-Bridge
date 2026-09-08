# Repository Model

AI Bridge uses a two-repository deployment model.

## 1. Shared product repository

This repository provides:

- Runtime
- Supervisor
- host adapters
- installer
- generic promoted knowledge
- protocol/specifications
- release artifacts

## 2. Per-install Bus repository

Created fresh for each installation.

Expected contents include:

- `PROJECT_STATE_INDEX.json`
- `AI_BRIDGE_READ_FIRST.md`
- `.ai-bridge/status/<bridge_id>.json`
- transport resources
- project authority documents created by that installation

A Bus repository must not receive a copied development-runtime branch or another user's project state.

## AI entrypoint

For every generated Bus, `PROJECT_STATE_INDEX.json` is the single machine entrypoint.
