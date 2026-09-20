# AI Bridge Transport Isolation P0

Runtime contract: 0.2.6.67+

## Transport scheduling

Dynamic V5 channels are concurrent transport lanes, not merely separate GitHub comments. The poll/control path persists receipts and durable ACKs promptly, then dispatches non-control commands into bounded execution lanes. Commands bound to the same Host Session are FIFO and never overlap. Different Host Sessions may execute concurrently. No-session Runtime adapters remain adapter-isolated; for the `workspace` adapter, Runtime 0.2.6.79+ refines the lane key to `adapter:workspace:<workspace_id>`, so different Workspaces may execute concurrently while commands inside the same Workspace remain FIFO. GitHub result publication remains serialized.

## Control plane

`bridge_transport / command.status` and `bridge_transport / transport.ping` execute on the control plane and do not wait behind Host execution lanes. `transport.ping` is the preferred live liveness probe.

## Durable status semantics

`.ai-bridge/status/<bridge_id>.json` is the last successfully published durable presence snapshot. It is authoritative for the contents and timestamp it reports, but a stale snapshot cannot prove current connectivity. For a live assertion, use `transport.ping`.

## Authentication degradation

GitHub 401/Bad credentials and permission-style 403 are transport authentication faults. Runtime/Core remain alive, public remote state becomes `auth_degraded`, retries use bounded exponential backoff, and a successful poll resets the failure count and returns to `connected`. Rate-limit failures remain a separate `rate_limited` state.

## BUSY_UNKNOWN

If a Host operation exceeds its execution budget and the Adapter may still be running it, that Session becomes `busy_unknown`. Subsequent session-bound Host operations fail closed with `SESSION_BUSY_UNKNOWN`. Heartbeats do not clear the state. Only the exact timed-out command's late Adapter result clears its tombstone/guard. A Host restart produces a new Session ID and therefore a clean execution state.

## Dashboard

Historical/disconnected sessions remain hidden from the user-facing Software Session panel. A `busy_unknown` Session is still live and therefore remains visible with an explicit warning state.

## Presence runtime-version authority

Runtime 0.2.6.80+ resolves durable Presence `bridge_version` from the active Runtime `pyproject.toml`. Transport extensions must preserve that value and must not hard-code or overwrite it. Runtime publication updates the version authority file; Presence fingerprinting then republishes the changed semantic state after activation.
