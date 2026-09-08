# AI Bridge — READ THIS FIRST

This repository is a per-install AI Bridge Bus.

## Mandatory entrypoint

Read:

- `main:PROJECT_STATE_INDEX.json`

before any Bridge or project operation.

The index tells you where to find:

- current live Bridge status;
- registered project authority documents;
- shared Runtime/product source;
- normative Bridge/AI protocol specifications.

## Authority

Use the precedence declared by `PROJECT_STATE_INDEX.json`.

Do not infer volatile Runtime/session/workspace state from static documentation.

Do not perform repository-wide discovery during normal recovery while indexed pointers are valid.

Do not replay a mutation merely because a previous chat response is missing; recover durable command/project state first.
