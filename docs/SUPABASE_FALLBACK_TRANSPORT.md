# Supabase Fallback Transport

## Status

Runtime `0.2.6.55` adds first-class Setup/Dashboard configuration for the optional Supabase-backed **secondary transport** introduced in `0.2.6.54`.

GitHub V5 remains primary. Supabase does not replace the Bus repository, project authority, product update authority, or `PROJECT_STATE_INDEX.json`.

The secondary transport is intentionally polled **in parallel** with GitHub. This targets the failure mode where the AI client can still read GitHub but its GitHub write action is unavailable.

## Architecture

```text
GitHub V5 Issue/Contents ─┐
                         ├─> TransportRunner -> BridgeDB -> Host Adapter
Supabase Data API ───────┘
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

Supabase projects created under the newer Data API defaults may not automatically expose SQL-created tables. The schema explicitly grants Data API table privileges required by the backend role.

## Normal Bridge activation — Setup UI

The normal user flow is through AI Bridge itself. Do not ask an interactive user to configure environment variables.

1. Connect the primary **GitHub Bus** first.
2. Open **AI Bridge Setup**.
3. In **Supabase Backup Bus**, enter:
   - Project URL, for example `https://<project-ref>.supabase.co`
   - `sb_secret_*` Secret Key
   - Poll interval; default `0.5s`
4. Click **Connect & Test / 连接并测试**.

Bridge ID is not entered twice. Runtime inherits the current GitHub Bus Bridge ID and uses the same ID for Supabase command routing.

Successful configuration performs a live Data API health check before persisting anything. Runtime stores only non-secret settings in:

`~/.ai_bridge/fallback_transport.json`

and stores the Secret Key through SecretStore/Windows DPAPI.

Subsequent edits can leave the Secret Key field blank to reuse the saved credential.

The Dashboard exposes a **Supabase Backup Bus** card with current status, Project URL, Bridge ID, poll interval and credential-saved state, plus:

- Modify configuration
- Test connection
- Disconnect and clear credential

Disconnect deletes both `fallback_transport.json` and the locally encrypted Supabase credential. It does not alter the Supabase project or GitHub Bus.

## Headless / recovery bootstrap

Environment variables remain supported only for headless deployment, emergency recovery, or automation where the Setup UI is unavailable:

```powershell
$env:AI_BRIDGE_SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:AI_BRIDGE_SUPABASE_SECRET_KEY = "sb_secret_..."
$env:AI_BRIDGE_SUPABASE_BRIDGE_ID = "<same bridge_id used by GitHub>"
$env:AI_BRIDGE_SUPABASE_TABLE = "ai_bridge_commands" # optional
$env:AI_BRIDGE_SUPABASE_POLL_INTERVAL = "0.5"         # optional
```

On successful health validation Runtime persists the same non-secret config and encrypted credential as the UI flow. The environment variable does not need to remain present for later restarts.

If no fallback config exists, Runtime reports the fallback as `unconfigured` and GitHub behavior is unchanged.

If fallback configuration fails, GitHub transport continues independently.

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

Bridge moves the row to `accepted` before host execution. The terminal `ExecutionResult` is then written to `result`, with `state` set to the result status.

## AI failover procedure

When GitHub writes fail:

1. Read GitHub live presence if reads still work.
2. Confirm `fallback_transport.status == "connected"`.
3. Preserve the original command envelope and `command_id`.
4. Insert the row into Supabase with `state = "queued"`.
5. Query that exact `command_id`.
6. Treat `accepted` as durable acceptance, not completion.
7. Wait for a terminal state and consume `result`.
8. If GitHub writes recover, do not replay the mutation. Keep the same `command_id` for recovery/fan-out.

If Supabase is not configured, do not claim failover is available.

## Failure behavior

- Supabase unavailable: its runner enters `error`; GitHub runner continues.
- GitHub unavailable: Supabase runner continues.
- malformed Supabase envelope: row becomes `rejected`.
- same ID / different canonical payload: fail closed; never intentionally reuse an ID for a different operation.
- Runtime restart after Supabase ACK: accepted rows are polled again so local durable state can resume/recover.
- result publication failure: TransportRunner keeps the terminal receipt pending and retries publication without re-executing host effects.

## Validation

Regression coverage includes:

- `runtime/tests/test_supabase_fallback_transport.py`
- `runtime/tests/test_supabase_ui_configuration.py`

It verifies Secret Key header behavior, legacy service-role compatibility, queue/ACK/result round-trip, malformed-envelope rejection, publishable-key rejection, DPAPI credential lifecycle, saved-key reuse, GitHub Bridge ID inheritance, connection retest, disconnect cleanup and Setup/Dashboard UI contracts.

The UI feature was developed TDD-first: its initial RED gate added four tests that failed only because the new lifecycle/UI methods were absent while all prior product tests remained green. The GREEN gate then passed the full Windows product test suite, `compileall`, deterministic release build/verification and PowerShell parsing.
