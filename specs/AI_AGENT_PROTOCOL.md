# AI Bridge — AI Agent Protocol

**Status:** Public integration contract

## 1. Purpose

This protocol lets an authorized AI agent enter an AI Bridge installation without relying on chat memory or manual explanation from the user.

## 2. Two repository roles

AI Bridge intentionally separates:

1. **Shared product repository** — `fmb22333-dev/AI-Bridge`
   - Runtime
   - Supervisor
   - host adapters
   - installer
   - generic distributable Knowledge Pack
   - public specifications

2. **Per-install Bus repository** — created for each user during first-run setup
   - `PROJECT_STATE_INDEX.json`
   - `AI_BRIDGE_READ_FIRST.md`
   - `.ai-bridge/status/<bridge_id>.json`
   - GitHub fallback transport resources
   - that user's project authority documents

The product repository is not authority for a user's live project/runtime state.

## 3. Mandatory AI entry sequence

When an AI is given access to a user's Bus repository:

1. Fetch `main:PROJECT_STATE_INDEX.json`.
2. Treat it as the single machine entrypoint.
3. Resolve the live runtime Presence/status pointer declared by the index.
4. Read `command_transport_policy` and transport state from Presence before sending a command.
5. Resolve the current project authority document only when a project is registered/selected.
6. Resolve normative Bridge specifications from the `runtime_source` declared by the index.
7. Use repository-wide search only if an indexed pointer is missing or invalid.

## 4. Authority order

Unless the generated index declares a newer compatible policy, use:

1. live runtime evidence / terminal command result
2. current project authority
3. normative Bridge specifications / promoted Knowledge Pack
4. historical handoff or chat memory

Static documentation must not override volatile live fields such as Runtime version, session IDs, workspaces, transport state, or write-block state.

## 5. Authorization and role model

GitHub and Supabase have deliberately different roles.

**GitHub is the durable authority and fallback command transport.** Repository permissions remain the security boundary for Presence discovery, project authority documents, Runtime/release metadata, documentation, Knowledge, source history, audit/recovery and GitHub V5 fallback commands.

**Supabase is the default realtime command/result transport when Presence reports it configured and connected.** The Bridge-side credential is a backend `sb_secret_*` key (or legacy service-role key during migration), stored locally through Bridge SecretStore/Windows DPAPI. Never commit or paste that key into either GitHub repository or expose it to a public/browser client.

An AI sender with an authorized Supabase connector may enqueue through the Supabase management/API surface without receiving the Bridge machine's secret key.

## 6. Transport discovery, multi-AI, and failover

Before sending a remote Bridge command, read live Presence. Prefer the semantic fields:

- `command_transport_policy`
- `supabase_transport`
- `message_transport` for GitHub V5 fallback state

When Presence reports:

```json
{
  "command_transport_policy": {
    "primary": "supabase",
    "fallback": "github_v5",
    "authority": "github"
  }
}
```

and `supabase_transport.status == "connected"`, normal commands go to Supabase first.

### Supabase primary sender rule

1. Create a globally unique canonical `command_id`.
2. Insert one row into the configured `ai_bridge_commands` table with `state = "queued"`.
3. The top-level row `command_id` must exactly equal `envelope.command_id`.
4. Treat `accepted` as durable acceptance but non-terminal execution state.
5. Consume terminal state/result from that same row.

Independent AI conversations submit independent rows. They do not own, share, or overwrite a single mailbox/comment.

Runtime scheduling remains safety-bounded:

- up to eight execution lanes when advertised;
- same Host Session commands are FIFO / serialized and never overlap;
- different Host Sessions may execute concurrently;
- unrelated no-session Runtime adapter lanes may execute concurrently;
- result publication remains serialized at each transport boundary.

Therefore one AI's long Houdini operation does not occupy the entire remote command bus, but another AI mutating that exact same Houdini Session still queues behind it.

### GitHub V5 fallback rule

If Supabase is unavailable/degraded, or if GitHub V5 is being tested explicitly:

1. do not mutate the canonical command or allocate a replacement identity;
2. preserve the same `CommandEnvelope` and `command_id`;
3. submit through an independent GitHub V5 channel/generation;
4. consume `AI_BRIDGE_ACK_V5` / `AI_BRIDGE_RESULT_V5` for that generation.

The same `command_id` may be observed through GitHub and Supabase. It is still one execution identity. Divergent payloads under one identity are invalid and must fail closed.

Runtime 0.2.6.x may keep historical `fallback_transport` naming as a compatibility alias for the Supabase state. New clients must use `supabase_transport` and `command_transport_policy` to infer roles.

Detailed current routing: `docs/SUPABASE_PRIMARY_TRANSPORT.md`.
Historical compatibility details: `docs/SUPABASE_FALLBACK_TRANSPORT.md`.

## 7. Command recovery

A missing chat-visible response is not permission to replay a mutation.

Before repeating a command:

- recover its durable state/result by the original `command_id`;
- if a Supabase row is `accepted` but non-terminal, do not duplicate it;
- if GitHub V5 has already ACKed the same command, do not duplicate it;
- if switching transports, keep the original `command_id`;
- if project state is stale, recover project state before mutation.

BridgeDB remains the local execution/idempotency authority across both transports.

## 8. Project targeting

Never guess a project, session, workspace, host, repository, or project file when more than one plausible target exists.

Project-specific paths and state belong only in the user's Bus/project authority documents, never in the public generic Knowledge Pack.

## 9. Knowledge model

The Adapter Kernel and Knowledge Pack are separate concepts.

- Kernel: transport, session, dispatch, checkpoint/rollback, typed primitives.
- Knowledge Pack: recipes, diagnostics, aliases, templates, host heuristics, validation rules.

Reusable knowledge follows a lifecycle such as:

`observed -> candidate -> validated -> promoted -> deprecated`

Only generic validated/promoted knowledge may enter the public distributable Knowledge Pack.

## 10. Safety rules

- Fail closed on malformed knowledge or invalid command arguments.
- Syntax sugar must not weaken child-operation risk/checkpoint/verification requirements.
- Do not silently bypass a promoted capability; record a fallback reason when fallback is required.
- Do not infer success from a missing error.
- Do not expose or commit secrets, local Bridge IDs from another installation, current sessions, command history, or project-specific evidence.
- Never use a Supabase publishable/anon key as a substitute for the Bridge backend credential.
- Multi-AI transport support does not authorize concurrent mutation of the same Host Session.

## 11. First-run result

A correctly initialized user installation must leave enough GitHub authority state that a new authorized AI can recover by being told the Bus repository location and the rule: **read `PROJECT_STATE_INDEX.json` and current Presence first**. Once discovered, the AI uses Supabase as the normal command path when connected and GitHub V5 as fallback.
