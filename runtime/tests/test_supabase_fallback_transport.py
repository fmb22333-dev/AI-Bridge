import json

import httpx

from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.supabase_bus import SupabaseBusConfig, SupabaseBusTransport


def _command(command_id="cmd-1"):
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {
            "verify": True,
            "checkpoint": "none",
            "dry_run": False,
        },
        "risk": "L1",
    }


def _transport(state, key="sb_secret_test"):
    def handler(request):
        assert request.url.path == "/rest/v1/ai_bridge_commands"
        assert request.headers["apikey"] == key
        if key.startswith("sb_"):
            assert "authorization" not in request.headers
        if request.method == "GET":
            select = request.url.params.get("select", "")
            if select == "command_id":
                return httpx.Response(200, json=[])
            command_id = request.url.params.get("command_id")
            state_filter = request.url.params.get("state")
            rows = []
            row = state.get("row")
            if row is not None:
                if command_id and command_id != f"eq.{row['command_id']}":
                    row = None
                if row is not None and state_filter == "in.(queued,accepted)" and row["state"] not in {"queued", "accepted"}:
                    row = None
            if row is not None:
                rows.append(dict(row))
            return httpx.Response(200, json=rows)
        if request.method == "PATCH":
            row = state.get("row")
            if row is None:
                return httpx.Response(200, json=[])
            command_id = request.url.params.get("command_id")
            if command_id != f"eq.{row['command_id']}":
                return httpx.Response(200, json=[])
            queued_only = request.url.params.get("state") == "eq.queued"
            if queued_only and row["state"] != "queued":
                return httpx.Response(200, json=[])
            body = json.loads(request.content.decode("utf-8"))
            row.update(body)
            return httpx.Response(200, json=[dict(row)])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return SupabaseBusTransport(
        SupabaseBusConfig(
            project_url="https://example.supabase.co",
            secret_key=key,
            bridge_id="bridge-test",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_supabase_fallback_fetch_ack_and_result_round_trip():
    state = {
        "row": {
            "command_id": "cmd-1",
            "bridge_id": "bridge-test",
            "state": "queued",
            "envelope": _command(),
            "result": None,
            "created_at": "2026-09-17T00:00:00Z",
        }
    }
    transport = _transport(state)
    assert transport.health().ok is True
    commands = transport.fetch_commands()
    assert [item.command_id for item in commands] == ["cmd-1"]
    assert transport.requires_durable_ack(commands[0]) is True
    receipts = transport.command_receipts("cmd-1")
    assert receipts[0]["ingress_kind"] == "supabase"

    transport.ack_command(commands[0])
    assert state["row"]["state"] == "accepted"
    assert state["row"]["accepted_at"]

    result = ExecutionResult(
        command_id="cmd-1",
        status=ExecutionStatus.SUCCESS,
        result={"ok": True},
    )
    transport.publish_result_for_receipt(result, receipts[0])
    assert state["row"]["state"] == "success"
    assert state["row"]["result"]["command_id"] == "cmd-1"
    assert state["row"]["result"]["result"] == {"ok": True}
    assert state["row"]["finished_at"]


def test_supabase_fallback_rejects_invalid_envelope():
    broken = _command()
    broken.pop("workspace")
    state = {
        "row": {
            "command_id": "cmd-1",
            "bridge_id": "bridge-test",
            "state": "queued",
            "envelope": broken,
            "result": None,
            "created_at": "2026-09-17T00:00:00Z",
        }
    }
    transport = _transport(state)
    assert transport.fetch_commands() == []
    assert state["row"]["state"] == "rejected"
    assert state["row"]["error"]["code"] == "INVALID_COMMAND_ENVELOPE"


def test_supabase_fallback_legacy_service_role_uses_bearer_auth():
    seen = {}

    def handler(request):
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json=[])

    key = "legacy-service-role-jwt"
    transport = SupabaseBusTransport(
        SupabaseBusConfig(
            project_url="https://example.supabase.co",
            secret_key=key,
            bridge_id="bridge-test",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert transport.health().ok is True
    assert seen["apikey"] == key
    assert seen["authorization"] == f"Bearer {key}"


def test_supabase_fallback_rejects_publishable_key():
    try:
        SupabaseBusTransport(
            SupabaseBusConfig(
                project_url="https://example.supabase.co",
                secret_key="sb_publishable_not_backend",
                bridge_id="bridge-test",
            )
        )
    except ValueError as exc:
        assert "publishable" in str(exc)
    else:
        raise AssertionError("publishable key must be rejected")
