import base64
import json

import httpx

import ai_bridge.transport.github_bus as gb
from ai_bridge.protocol.command import CommandEnvelope


def _command(command_id="cmd-contents-only"):
    return CommandEnvelope.model_validate(
        {
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
    )


def test_comments_denied_contents_mode_keeps_receipt_and_durable_acceptance_semantics():
    command = _command()
    raw = json.dumps(command.model_dump(mode="json")).encode()

    def handler(request):
        path = request.url.path
        headers = {"ETag": '"fallback-v1"'}
        if request.method == "GET" and path.endswith("/issues/comments"):
            return httpx.Response(403, headers=headers, json={"message": "Forbidden"})
        if request.method == "GET" and path.endswith(
            "/contents/.ai-bridge/commands/bridge-test"
        ):
            return httpx.Response(
                200,
                headers=headers,
                json=[
                    {
                        "type": "file",
                        "name": f"{command.command_id}.json",
                        "sha": "sha-command",
                    }
                ],
            )
        if request.method == "GET" and path.endswith(
            "/contents/.ai-bridge/results/bridge-test"
        ):
            return httpx.Response(404, headers=headers, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith(f"/{command.command_id}.json"):
            return httpx.Response(
                200,
                headers=headers,
                json={"content": base64.b64encode(raw).decode("ascii")},
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = gb.GitHubBusTransport(
        gb.GitHubBusConfig(
            repository="owner/repo",
            token="x",
            bridge_id="bridge-test",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert transport.initialize_message_mode() == "contents"
    first = transport.fetch_commands()
    assert [item.command_id for item in first] == [command.command_id]
    assert transport.command_receipts(command.command_id)[0]["ingress_kind"] == "contents"
    assert transport.requires_durable_ack(command) is True

    second = transport.fetch_commands()
    assert second == []
    assert transport.command_receipts(command.command_id)[0]["ingress_kind"] == "contents"
