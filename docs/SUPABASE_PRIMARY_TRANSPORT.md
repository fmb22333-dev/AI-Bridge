# Supabase Primary Transport

**Status:** normative realtime transport contract for Runtime 0.2.6.56+.

## Roles

- **Supabase** — primary realtime command/result transport.
- **GitHub V5** — fallback command transport.
- **GitHub Bus** — durable Presence, project/document/release authority.
- **BridgeDB** — local execution/idempotency authority keyed by canonical `command_id`.

## Discovery and routing

1. Read the user Bus `PROJECT_STATE_INDEX.json`.
2. Read the indexed durable Presence.
3. When current reachability matters, verify with `bridge_transport / transport.ping`.
4. Read semantic Presence fields `command_transport_policy` and `supabase_transport`.
5. If Supabase is primary and connected, insert the canonical `bridge/1` envelope into `public.ai_bridge_commands`.
6. Treat `accepted` as durable acceptance, not completion.
7. If Supabase cannot be used, submit the **same envelope with the same `command_id`** through GitHub V5.
8. Never replay a Host mutation merely because a chat-visible result is missing.

## Table contract

Default table: `public.ai_bridge_commands`.

Required columns:

- `command_id text primary key`
- `bridge_id text not null`
- `envelope jsonb not null`
- `state text not null`
- `result jsonb`
- `error jsonb`
- `created_at / accepted_at / finished_at / updated_at timestamptz`

Runtime states include `queued`, `accepted`, `success`, `failed`, `denied`, `conflict`, `unknown` and `rejected`.

Apply [supabase_fallback_schema.sql](supabase_fallback_schema.sql). The filename is historical and retained for compatibility; the schema is for the current primary transport.

The row's top-level `command_id` must exactly equal `envelope.command_id`.

## Multi-AI scheduling

Supabase uses one row per command. Runtime scheduling is bounded:

- up to 8 execution lanes when advertised;
- same Host Session: FIFO/serialized;
- different Host Sessions: may execute concurrently;
- unrelated no-session Runtime lanes: may execute concurrently;
- same Workspace lane: serialized;
- result publication: serialized per transport boundary.

Multi-AI support does not authorize concurrent mutation of the same Host Session.

## Setup and credentials

Normal interactive setup:

1. connect GitHub Bus first so durable authority and Bridge ID exist;
2. configure **Supabase Primary Bus** with Project URL, backend Secret Key and poll interval;
3. Bridge reuses the GitHub-authority Bridge ID and performs a live Data API health check before persistence.

Use a backend secret (`sb_secret_*`; legacy service-role keys remain compatibility-only). Do not use publishable/anon keys for the Bridge backend.

The secret is stored through local SecretStore/Windows DPAPI. Non-secret settings remain in the historical `fallback_transport.json` filename during 0.2.6.x compatibility.

Headless/recovery environment variables remain:

- `AI_BRIDGE_SUPABASE_URL`
- `AI_BRIDGE_SUPABASE_SECRET_KEY`
- `AI_BRIDGE_SUPABASE_BRIDGE_ID`
- `AI_BRIDGE_SUPABASE_TABLE`
- `AI_BRIDGE_SUPABASE_POLL_INTERVAL`

## Failure semantics

- Supabase unavailable/degraded: GitHub V5 remains the command fallback.
- Same `command_id` with divergent canonical payload: fail closed.
- Runtime restart after durable acceptance: resume/recover from durable state; do not authorize a second Host effect.
- Result publication failure: retry publication without re-executing the Host mutation.

## Compatibility names

Runtime 0.2.6.x intentionally retains historical implementation/API names such as `SupabaseFallbackController`, `SupabaseFallbackConfig`, `fallback_transport.json`, `/control/fallback/*`, `fallback_transport` and DOM id `supabaseBackupCard`.

These are compatibility surfaces only. New clients must infer roles from `supabase_transport` and `command_transport_policy`.

Measured transport performance is historical evidence, not routing authority. Presence is the routing authority.
