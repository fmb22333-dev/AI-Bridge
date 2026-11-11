# Workspace files through global Bridge MCP

The five global tools stay the same. `ta_bridge_context()` lists the exposed
workspace operations. Query `ta_bridge_context(operation="workspace.patchset.apply")`
for its exact `arguments_schema`; every exposed workspace operation supports this.
Only operations advertised by the current Runtime are usable.

Submit with `adapter="workspace"`, an explicit registered `workspace`, a stable
`command_id`, `operation`, and `arguments`. Omit `session_id` and `project_file`.
Poll the original command through `ta_bridge_status` and retrieve its receipt with
`ta_bridge_result`. An accepted submission is not proof that the files changed.

## Operations

- `workspace.directory.list`: `path` (default `.`), `max_entries` (default 200, max 1000).
- `workspace.file.read`: `path`, optional `max_chars` (default 100000, max 500000).
- `workspace.files.read`: `paths` (1–64), optional `max_chars_per_file` and
  `max_total_chars` (total max 500000). Each item reports text, truncation, sha256 or an error.
- `workspace.file.create`: `path`, UTF-8 `text`, optional `create_parents`.
  Never overwrites different existing content.
- `workspace.file.patch`: `path`, preferably `expected_hash`, and either
  `old_text` / `new_text` or `edits` (1–32 sequential replacements). Each old span
  must match uniquely. Single-file patching accepts equivalent LF/CRLF spans.
- `workspace.patchset.apply`: `changes` (1–64), optional `post_commands`.
- Existing `workspace.project.inspect` and `workspace.command.run` remain available.
- `workspace.project.configure`: register/update execution commands using a full
  structured `manifest`, plus `expected_hash` when a manifest already exists.
  It configures definitions only; use `workspace.command.run` to execute a name.

## Register an execution command

Query `ta_bridge_context(operation="workspace.project.configure")` for the schema.
Read `.ai-bridge/project.json` with `workspace.file.read`. Preserve its project,
other commands and services, add/update the intended entry in `commands`, then
submit the full manifest with the returned `sha256` as `expected_hash`.
For a new manifest, supply `project`, `commands`, `services` and omit the hash.
The backend validates executable `argv` arrays, relative `cwd`, environment and
timeouts. A stale or missing hash on an existing manifest is rejected.

Example arguments for a new manifest (use the intended installed executable):

```json
{"manifest":{"schema_version":"1.0","project":"Example","commands":{"check":{"argv":["python","-c","print('ok')"],"cwd":".","timeout_seconds":30}},"services":{}}}
```

This is a full manifest replacement, not a merge. Omitting existing entries removes
their definitions. Only register executable arguments within the user's authorized
task. Generic file writes still cannot edit control files; no workspace registration
or service start/stop endpoints are exposed by this extension.

## Batch changes

Read before modifying an existing file; copy the returned full-file `sha256` into
`expected_hash`. A truncated read still hashes the whole file; inspect the relevant
content before constructing a replacement. On conflict, read again and reassess.

Example batch arguments (replace HASH_FROM_READ with the actual SHA-256):

```json
{
  "changes": [
    {"op":"patch","path":"Source/example.py","expected_hash":"HASH_FROM_READ","old_text":"old_value","new_text":"new_value"},
    {"op":"create","path":"Docs/change.txt","text":"Updated example","create_parents":true}
  ]
}
```

Batch change forms: `create` needs `text`; `patch` needs `expected_hash`, unique
exact `old_text` and `new_text`; `replace` needs `expected_hash` and `text`;
`delete` needs `expected_hash`. No duplicate paths. Existing-file hashes are
mandatory for batches. New content is limited to 2000000 UTF-8 bytes per file and
4000000 bytes per batch; read limits count characters, write limits count bytes.

All changes are checked before writing. Write or post-command failure attempts to
restore the batch's files; inspect `rolled_back` in the receipt. This is not an
OS-wide transaction against other editors, process termination, or command side
effects. Coordinate edits to the same files. `post_commands` only accepts existing
project-manifest command names, not shell text; use it only when those commands
are authorized. Its external effects and outputs are not rolled back.

Paths stay within the selected workspace, including resolved link targets.
`.ai-bridge` control files remain protected from generic file writes. Structured
project configuration is available for command registration as described above.
Workspace registration and service administration remain outside these tools.
Text APIs do not edit UE binary assets; use the appropriate Host capability.

Existing MCP processes must be reloaded to pick up the updated helper and context
schema. No Runtime, UE or Houdini restart is needed for this MCP-only extension.
