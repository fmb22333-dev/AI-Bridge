# Runtime 0.2.6.56 — Supabase Primary Multi-AI Transport

Date: 2026-09-17

## Change

Supabase is promoted from optional backup ingress to the default realtime command/result transport. GitHub V5 remains the fallback command transport and the durable authority for Presence, project state, Runtime/release metadata, documentation, Knowledge, source history and audit/recovery.

## Concurrency

Supabase now advertises `multi_channel=true` and `multi_ai=true`, activating a bounded 8-worker lane scheduler. Same Host Session commands remain FIFO/serialized; unrelated Sessions and Runtime adapter lanes may progress concurrently. Transport result publication is serialized.

## Compatibility

0.2.6.x keeps fallback-named config/controller/API fields so existing installations upgrade without credential migration. New semantic discovery is through `supabase_transport` and `command_transport_policy`.

## Live acceptance evidence

The developer live branch accepted equivalent semantics as Runtime 0.2.6.77 before public backport:

- Supabase-specific primary-role regression: 7/7 PASS;
- Transport Isolation + GitHub V5 multichannel regression: 16/16 PASS;
- full live Runtime: 472/472 PASS;
- compile PASS;
- Knowledge publish gate PASS;
- Supervisor activation PASS; rollback retained.

## Performance evidence

10× `transport.ping` per transport on 2026-09-17:

- Supabase ingress P50 ~0.406s, P95 ~0.702s;
- GitHub V5 ingress P50 ~3.34s, P95 ~5.51s.
