# Supabase Fallback Transport — Compatibility Note

Supabase stopped being the secondary/fallback transport in Runtime **0.2.6.56**. The current routing contract is [SUPABASE_PRIMARY_TRANSPORT.md](SUPABASE_PRIMARY_TRANSPORT.md).

This file exists only to explain retained 0.2.6.x compatibility names:

- `SupabaseFallbackController`
- `SupabaseFallbackConfig`
- `fallback_transport.json`
- `/control/fallback/*`
- `fallback_transport` Presence compatibility field
- DOM id `supabaseBackupCard`
- `supabase_fallback_schema.sql`

These names do **not** mean Supabase is currently the fallback. Current semantics are:

```text
Supabase  -> primary realtime command/result
GitHub V5 -> fallback command transport
GitHub    -> durable Presence/project/document/release authority
BridgeDB  -> local execution/idempotency authority
```

Do not duplicate routing, security, schema or setup instructions here. Use the primary transport document as the single current authority.
