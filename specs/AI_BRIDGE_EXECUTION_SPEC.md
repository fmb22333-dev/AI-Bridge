# AI Bridge — Public Execution Specification

**Status:** Normative public contract

## Goal

AI Bridge is a local execution bridge controlled through an authorized GitHub Bus when no suitable inbound endpoint is available. Host execution remains local; GitHub is the durable remote control/state boundary.

Supported direction:

- Houdini
- Unreal Engine
- Blender
- future host adapters

## Architecture

`AI -> user GitHub Bus -> local Bridge Runtime -> host Adapter -> host application -> durable result -> GitHub Bus -> AI`

The shared product repository and the user's Bus are separate.

## Runtime-state authority

Never take current Runtime version, active session IDs, workspaces, transport mode or write-block state from this static document. Read the live status pointer from the user's `PROJECT_STATE_INDEX.json`.

## Transport selection

Use the transport advertised by live status. Current implementations may expose independent Issue Comment channels for concurrent AI conversations and compatibility fallbacks.

Independent AI conversations must never share a mutable command slot when a multi-channel transport is available.

A sender must preserve command identity/generation ownership and must not overwrite a newer command with an older result.

## Interruption recovery

A missing visible result after an AI/chat interruption does not authorize command replay.

Recover durable status/result first. If the original mutation was accepted but is non-terminal, do not duplicate it. If project recovery reports stale state, refresh state before further mutation.

## Session targeting

Host sessions are ephemeral. Never hardcode an old session ID.

Before host execution:

1. read live Bridge status;
2. resolve the intended connected host session;
3. bind the command to that session;
4. include project/file guards when required by the operation.

Never guess between multiple plausible projects/sessions/workspaces.

## Operation safety

Operations should expose explicit risk and verification semantics.

Typical model:

- L1: read/low-risk UI state
- L2: reversible graph/property mutation
- L3+: increasingly consequential mutation/host process control

For writes, preserve optimistic concurrency (`expected_hash` or equivalent) where provided. Stale state must fail closed rather than overwrite newer human/AI edits.

Risky structural mutations should checkpoint/rollback where supported and verify readback.

## Syntax sugar / recipes

Higher-level recipes are bounded compositions of typed primitives. They must not weaken child-operation risk, checkpoint, target, concurrency or verification requirements.

Required behavior:

- bounded step/item/time budgets;
- no unbounded recursion/retry;
- required-step failure surfaces structurally;
- mutation stops on required-step failure;
- rollback availability is explicit;
- execution trace is bounded and sufficient for diagnosis;
- candidate recipes are not normal production authority until promoted.

## Host threading

Host APIs must run on the host-safe execution thread. In Houdini, normal `hou` graph mutation belongs on the Houdini UI/main-thread handoff, not arbitrary background workers.

Command polling and result egress should remain decoupled so a new long poll cannot delay a completed result.

## Runtime updates

Runtime update/build is separate from live host-project work. Updates stage and validate before activation. A failed activation must retain/restore the prior healthy Runtime where supported.

Building the public repository is not permission to activate it on a developer machine.

## GitHub security

GitHub permissions are the remote authorization boundary. Credentials stay in local secret storage and are never committed to the product or Bus repository.

## Self-description

Adapters should expose capability discovery/argument schemas where practical. AI agents should prefer promoted generic capabilities that fully cover intent before composing multiple low-level commands.

When falling back from a promoted capability, preserve the reason in execution evidence rather than silently bypassing it.
