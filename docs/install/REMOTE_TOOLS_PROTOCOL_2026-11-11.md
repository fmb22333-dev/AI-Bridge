# Remote Bridge command discovery and complete saved logs

Send commands through the existing Supabase ai_bridge_commands table using your
normal authenticated channel. No local MCP installation is needed for remote use.
The canonical CommandEnvelope and command_id remain the authority.

## Discover the current contract

Existing `transport.ping` and published Bridge presence include `remote_controls`.
Follow its `transport.capabilities` operation to read schemas and examples.
Existing `work_hub / capability.search` also finds `command.log`, `command.cancel`,
`command.status`, `transport.context` and `transport.capabilities`.
These search matches describe direct transport envelopes; do not pass their targets
to `capability.run`. `transport.context` returns live Host/project/session targeting.

Example envelope for discovering the current command schemas:

```json
{
  "protocol": "bridge/1",
  "command_id": "unique-capability-query-1",
  "workspace": "Bridge",
  "adapter": "bridge_transport",
  "operation": "transport.capabilities",
  "arguments": {},
  "risk": "L1"
}
```

Use a fresh ID for each distinct query or page; preserve the same ID and content
when resolving an uncertain submission. Target the original task through
`arguments.command_id`, rather than reusing its envelope ID for status queries.

## Read a full saved process log

Send `bridge_transport / command.log` with:

```json
{"command_id":"original-task-id","offset":0,"count":3072}
```

The result contains `data_base64`, `chunk_sha256`, `offset`, `next_offset`,
`total_bytes_observed`, `eof_at_read` and `terminal`. Count is a byte limit from
1 to 3072; default 2048. Small pages remain below transport compaction thresholds.

Decode base64 into bytes, verify each chunk hash, concatenate in offset order and
advance to next_offset. Decode UTF-8 after concatenation because a byte page can
end inside a multibyte character. Do not repeatedly append a replayed page.
An empty page with eof_at_read means there are currently no more bytes; a running
task can append more later. Query command.status to distinguish that from completion.

These are the complete saved, redacted stdout/stderr bytes of a Bridge-owned process,
not an unredacted copy or an arbitrary local file reader. Historical/native commands
without a saved process log return NO_SAVED_PROCESS_LOG. Host-internal logs continue
to use their advertised Host capabilities, for example read.editor_log.

## Cancel and read the receipt

Send `bridge_transport / command.cancel` with:

```json
{"command_id":"original-task-id","close_host":false}
```

Default false cancels only the owned process tree. True additionally requests normal
closure of the originally bound Host, if that identity was recorded. It never chooses
another project or forces past unsaved-work prompts. A cancellation request is not
proof of exit: query command.status with include_result=true afterward.

The deployed Supabase table accepts a smaller set of state strings than Bridge Core.
A canonical skipped receipt (including TASK_CANCELLED) is indexed as state=failed
in that table. Read result.status and result.failure.code for the precise outcome;
the full canonical receipt is preserved unchanged.

## Local tool mapping

| Local MCP | Remote envelope |
| --- | --- |
| ta_bridge_context | transport.context |
| ta_bridge_submit | original registered operation |
| ta_bridge_status | command.status |
| ta_bridge_result | command.status with include_result=true |
| ta_bridge_cancel | command.cancel |

