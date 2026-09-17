# Supabase Primary Transport

Status: Runtime `0.2.6.56+` public product contract.

## Roles

- **Supabase** — default realtime command/result transport (`PRIMARY_FAST`).
- **GitHub V5** — fallback command transport and durable authority.
- **BridgeDB** — local execution/idempotency authority keyed by canonical `command_id`.

GitHub remains authoritative for Presence discovery, Runtime/release metadata, project authority documents, Knowledge, documentation, source history, recovery and audit material. Moving realtime commands to Supabase does not move those authorities.

## Routing

1. Read GitHub Presence first.
2. Read `command_transport_policy` and `supabase_transport`.
3. When `primary=supabase` and Supabase is `connected`, insert the canonical `bridge/1` command into `public.ai_bridge_commands`.
4. Wait on that exact row until terminal state. `accepted` means durable acceptance, not completion.
5. If Supabase cannot be used, send the **same command with the same `command_id`** through GitHub V5.
6. Never create a new execution identity merely because the transport changed.

## Multi-AI contract

Supabase uses one row per command, so independent AI clients do not share or overwrite a mailbox. The Runtime scheduler provides bounded execution lanes:

- up to 8 execution workers;
- same Host Session: FIFO / serialized;
- different Host Sessions: may execute concurrently;
- unrelated no-session Runtime adapter lanes: may execute concurrently;
- result publication remains serialized at the transport boundary.

This prevents one AI conversation from occupying the entire command bus while preserving Host safety for Houdini/UE sessions.

## Compatibility

Runtime 0.2.6.x retains these historical names as compatibility surfaces:

- `SupabaseFallbackController`;
- `SupabaseFallbackConfig`;
- `fallback_transport.json`;
- `/control/fallback/*`;
- `fallback_transport` Presence field;
- DOM id `supabaseBackupCard`.

New clients should use semantic fields `supabase_transport` and `command_transport_policy` rather than inferring roles from historical names.

## Security

Use a backend secret (`sb_secret_*`; legacy service-role compatibility only). Never expose that credential to browser/public clients or Git. AI Bridge stores it via SecretStore / Windows DPAPI on supported Windows installations.

## Measured basis

2026-09-17 live 10× `transport.ping` measurement on the same Bridge:

- Supabase ingress P50 ≈ **0.406 s**, P95 ≈ **0.702 s**;
- GitHub V5 ingress P50 ≈ **3.34 s**, P95 ≈ **5.51 s**;
- ping execution itself ≈ **0.03–0.07 ms**.

The measured latency gap is why GitHub V5 was removed from the normal realtime critical path.
