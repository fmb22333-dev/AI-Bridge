# AI Bridge — Public Execution Specification

**Status:** normative public execution contract.

## Authority

Current live Runtime/session/workspace/transport/write-block state comes from the user's indexed live state, not this static document.

Authority order:

1. terminal live Runtime/command evidence;
2. current project authority;
3. normative product specifications and promoted Knowledge;
4. historical handoff/chat.

A generated user Bus uses `PROJECT_STATE_INDEX.json` as the machine entrypoint.

## Architecture

```text
AI
 |
 | read GitHub PROJECT_STATE_INDEX + durable Presence
 v
discover command_transport_policy
 |
 +------------------+
 |                  |
 v                  v
Supabase PRIMARY   GitHub V5 FALLBACK
 |                  |
 +--------+---------+
          v
      BridgeDB
          |
   Bridge Runtime
          |
     Host Adapter
          |
 Houdini / Unreal
```

GitHub remains durable Presence/project/document/release authority even when Supabase carries normal realtime commands.

## Command identity and recovery

`command_id` is the canonical execution identity across transports.

- Preserve it during failover.
- `accepted` means durably accepted but may still be non-terminal.
- A missing chat-visible result never authorizes replay.
- Recover durable command/project state before repeating a mutation.
- Same ID with divergent canonical payload must fail closed.

## Targeting and concurrency

Host sessions are ephemeral. Resolve the intended live session before execution and never guess between plausible projects, sessions or workspaces.

Scheduling is safety-bounded:

- same Host Session: FIFO/serialized;
- different Host Sessions: may execute concurrently;
- unrelated Runtime lanes: may execute concurrently;
- same Workspace lane: serialized where advertised.

Multi-AI transport support does not authorize overlapping mutation of one Host Session.

## Operation safety

Operations expose risk/verification semantics. Writes should use optimistic concurrency guards where available. Stale state fails closed. Structural mutations should checkpoint/rollback and independently verify readback when supported.

Execution budgets are bounded. A Host operation that times out while the Host may still be running it can enter `busy_unknown`; later commands for that Session must fail closed until exact late-result or Host-session replacement resolves the ambiguity.

## Capabilities and recipes

Prefer a promoted generic capability when it fully covers the intent. Higher-level recipes must preserve child-operation risk, checkpoint, target, concurrency and verification guarantees.

Candidate/unpromoted recipes are not normal production authority. Fallback from a promoted capability should record why fallback was necessary.

## Host threading

Host APIs execute on the Host-safe thread/context. Houdini graph mutation, for example, belongs on the Houdini UI/main-thread handoff rather than arbitrary Runtime worker threads.

Transport polling/result egress must remain decoupled from long Host execution so unrelated control-plane operations such as `transport.ping` and `command.status` remain responsive.

## Runtime and plugin updates

Product updates are distinct from user project work. Candidate Runtime updates stage, validate and activate through Supervisor-managed health/rollback boundaries.

Building or publishing this repository is not permission to mutate a developer's live Host project.

Host plugin installation must follow each Host's safety rules. Unreal project-scoped AIBridgeUE installation does not automatically close/restart Unreal Editor.

## Security

GitHub and Supabase credentials remain in local secret storage and are never committed to product or Bus repositories. Supabase backend transport requires a backend secret; publishable/anon credentials are not substitutes.

## Related normative documents

- `specs/AI_AGENT_PROTOCOL.md`
- `specs/AI_BRIDGE_DEPLOYMENT_SPEC.md`
- `specs/AI_BRIDGE_LEARNING_POLICY.md`
- `docs/SUPABASE_PRIMARY_TRANSPORT.md`
