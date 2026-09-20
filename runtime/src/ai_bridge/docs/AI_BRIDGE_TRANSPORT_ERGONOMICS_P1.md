# AI Bridge Transport Ergonomics P1

Runtime contract: **0.2.6.68+**

## Structured V5 result delivery

Large owned-V5 results use a recursive structured summary rather than dropping complete top-level values. The summary preserves scalar/status/error/count/hash/digest metadata where possible, truncates long strings with original character counts, samples large lists with count/head/tail, and reports exact `truncated_paths` / `omitted_paths`.

The canonical full terminal `ExecutionResult` remains in the local Runtime command database. Ordinary large owned-V5 delivery does **not** create a GitHub Contents archive. The summary advertises recovery through:

- adapter: `bridge_transport`
- operation: `command.status`
- arguments: `{"command_id": "<source>", "include_result": true}`

Explicit full recovery is two-tiered:

- recovery envelopes up to the bounded Comment limit are returned in full;
- recovery envelopes above that limit create a one-time on-demand Contents archive and return `delivery.mode=full_reference` with `full_result_ref`.

Durable Contents fallback remains mandatory when the owning Comment generation changed or the result Comment PATCH fails. That fallback uses the original full terminal result, never the structured presentation summary.

## V5 predecessor guard

A V5 command wrapper may optionally include:

```json
"predecessor": {
  "command_id": "cmd-A",
  "require": "terminal"
}
```

`require` is `terminal` or `success` and defaults to `success`. This is transport sequencing metadata and is intentionally not part of `CommandEnvelope` or Host Adapter schemas.

The guard is evaluated before durable accept/ACK/dispatch. Rejection codes are:

- `INVALID_PREDECESSOR`
- `PREDECESSOR_NOT_FOUND`
- `PREDECESSOR_NOT_TERMINAL`
- `PREDECESSOR_NOT_SUCCESS`

A rejected successor is not accepted into execution. The sender must submit a fresh generation after the dependency condition is satisfied. There is no automatic deferred dependency queue.

Once a command has been ACKed, the predecessor condition was already satisfied at initial acceptance; ACK recovery after Runtime interruption therefore does not re-run the predecessor gate.

## Compatibility

- Existing V5 wrappers without `predecessor` are unchanged.
- `CommandEnvelope` remains unchanged.
- V4/V3/V2/Contents fallbacks remain compatible.
- `capability.search` retains its specialized compact representation.
- Transport Isolation 0.2.6.67 rules remain authoritative: same Session FIFO, cross-Session concurrency, control-plane ping/status, auth degradation, BUSY_UNKNOWN.

## Sender guidance

Use `predecessor` only for genuinely dependent commands. Independent commands should remain independent. Do not use a predecessor merely to serialize an entire conversation.

For a structured summary, consume the available summary first. Request the full terminal result only when the omitted/truncated evidence is actually needed.
