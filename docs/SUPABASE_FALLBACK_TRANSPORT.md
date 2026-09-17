# Supabase Fallback Transport

> **Role update (Runtime 0.2.6.56+):** Supabase is now the default realtime command/result transport. GitHub V5 is the fallback command transport and remains the durable authority. This file documents the original fallback implementation and the compatibility names that remain in 0.2.6.x. For the current routing contract, read [`SUPABASE_PRIMARY_TRANSPORT.md`](SUPABASE_PRIMARY_TRANSPORT.md).

## Historical status

Runtime `0.2.6.55` added first-class Setup/Dashboard configuration for the optional Supabase-backed secondary transport introduced in `0.2.6.54`. Runtime `0.2.6.56` promotes the same transport to primary-fast semantics without breaking the existing config file, controller class, API paths, or credential store.

GitHub continues to own the Bus repository, project authority, product update authority, documentation, Knowledge, Presence discovery and `PROJECT_STATE_INDEX.json`.

## Architecture

```text
Supabase Data API ───────┐  primary realtime command/result path
                         ├─> TransportRunner -> BridgeDB -> Host Adapter
GitHub V5 Issue/Contents ┘  fallback command path + durable authority
```

Both ingress paths use the existing `bridge/1` `CommandEnvelope`.

`command_id` is the canonical execution identity. BridgeDB remains the execution authority. If the same command reaches both transports, the durable command/result is reused instead of authorizing a second host-side mutation.

Do **not** create a new command ID merely because transport failover occurred.

## Table contract

Default table: `public.ai_bridge_commands`.

Required columns:

- `command_id text primary key`
- `bridge_id text not null`
- `envelope jsonb not null`
- `state text not null`
- `result jsonb`
- `error jsonb`
- `created_at timestamptz`
- `accepted_at timestamptz`
- `finished_at timestamptz`
- `updated_at timestamptz`

States used by Runtime:

`queued -> accepted -> success|failed|denied|conflict|unknown`

Malformed rows are terminally marked `rejected`.

Apply [`docs/supabase_fallback_schema.sql`](supabase_fallback_schema.sql) to a dedicated Supabase project.

## Security model

Use a dedicated Supabase project when practical.

Bridge backend access requires a **secret/backend** key:

- preferred: `sb_secret_*`
- compatibility only: legacy JWT-style `service_role`

Do not use `sb_publishable_*`/anon keys for Bridge backend transport.

New `sb_secret_*` keys are sent in the `apikey` header only. Legacy JWT-style service-role keys use both `apikey` and `Authorization: Bearer`.

The secret is stored on the Bridge machine through the existing SecretStore (Windows DPAPI in the supported Windows runtime). It is never stored in `fallback_transport.json`, the Bus repository, or this product repository. Setup uses a password input and never returns the saved key through status/test APIs.

The SQL enables RLS and grants only the backend `service_role`. No public row policy is created.

## Normal Bridge activation — Setup UI

The normal user flow is through AI Bridge itself. Do not ask an interactive user to configure environment variables.

1. Connect **GitHub Bus · Backup + Authority** first so the Bridge ID and durable authority are established.
2. Open **AI Bridge Setup**.
3. In **Supabase Primary Bus**, enter:
   - Project URL, for example `https://<project-ref>.supabase.co`
   - `sb_secret_*` Secret Key
   - Poll interval; default `0.5s`
4. Click **Connect & Test / 连接并测试**.

Bridge ID is not entered twice. Runtime inherits the GitHub authority Bridge ID and uses the same ID for Supabase command routing.

Successful configuration performs a live Data API health check before persisting anything. Runtime stores only non-secret settings in:

`~/.ai_bridge/fallback_transport.json`

The historical filename is retained for 0.2.6.x compatibility. The Secret Key is stored through SecretStore/Windows DPAPI.

The Dashboard exposes a **Supabase Primary Bus** card plus GitHub **Backup + Authority** status.

## Headless / recovery bootstrap

Environment variables remain supported for headless deployment, emergency recovery, or automation where the Setup UI is unavailable:

```powershell
$env:AI_BRIDGE_SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:AI_BRIDGE_SUPABASE_SECRET_KEY = "sb_secret_..."
$env:AI_BRIDGE_SUPABASE_BRIDGE_ID = "<same bridge_id used by GitHub>"
$env:AI_BRIDGE_SUPABASE_TABLE = "ai_bridge_commands" # optional
$env:AI_BRIDGE_SUPABASE_POLL_INTERVAL = "0.5"         # optional
```

## Sender row format

An authorized sender inserts:

```json
{
  "command_id": "cmd-example-001",
  "bridge_id": "bridge-example",
  "state": "queued",
  "envelope": {
    "protocol": "bridge/1",
    "command_id": "cmd-example-001",
    "workspace": "Bridge",
    "adapter": "bridge_admin",
    "operation": "bridge.update.status",
    "arguments": {},
    "execution": {
      "verify": true,
      "checkpoint": "none",
      "dry_run": false
    },
    "risk": "L1"
  }
}
```

The top-level `command_id` **must exactly match** `envelope.command_id`.

Bridge moves the row to `accepted` before Host execution. The terminal `ExecutionResult` is then written to `result`, with `state` set to the result status.

## Current failover procedure

1. Read GitHub Presence/discovery.
2. If `supabase_transport.status == "connected"`, use Supabase as the normal command path.
3. If Supabase is unavailable, preserve the original canonical envelope and `command_id`.
4. Send that same command through GitHub V5.
5. Do not replay a Host mutation after either transport has produced durable acceptance/terminal evidence.

## Failure behavior

- Supabase unavailable: its runner enters `error`; GitHub V5 remains available.
- GitHub command-write unavailable: Supabase primary continues.
- malformed Supabase envelope: row becomes `rejected`.
- same ID / different canonical payload: fail closed.
- Runtime restart after Supabase ACK: accepted rows are polled again so local durable state can resume/recover.
- result publication failure: TransportRunner retries publication without authorizing a second Host effect.

## Validation

Regression coverage includes:

- `runtime/tests/test_supabase_fallback_transport.py`
- `runtime/tests/test_supabase_ui_configuration.py`
- `runtime/tests/test_supabase_primary_multi_ai.py`

The primary promotion adds bounded lane scheduling with up to eight workers: same Host Session FIFO, different Sessions / Runtime adapter lanes may execute concurrently, and transport result publication remains serialized.
