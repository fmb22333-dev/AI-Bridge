import json

import httpx

import ai_bridge.transport.github_bus as gb


def test_v5_ack_with_embedded_command_is_recoverable_after_restart():
    command = {
        "protocol": "bridge/1", "command_id": "cmd-recover", "workspace": "Bridge",
        "adapter": "bridge_admin", "operation": "bridge.update.status", "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False}, "risk": "L1",
    }
    ack = gb.CHANNEL_ACK_MARKER_V5 + "\n" + json.dumps({
        "bridge_id": "bridge-test", "channel_id": "chat-a", "generation": "gen-a",
        "command_id": "cmd-recover", "command": command, "state": "accepted",
    })
    mailbox = gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"})
    state = {100: mailbox, 201: ack}
    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, headers={"ETag": '"v1"'}, json=[
                {"id": cid, "issue_url": "https://api.github.com/repos/owner/repo/issues/1", "body": body}
                for cid, body in state.items()
            ])
        raise AssertionError(f"unexpected {request.method} {request.url}")
    transport = gb.GitHubBusTransport(
        gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert transport.initialize_message_mode() == "issue_channel_v5"
    commands = transport.fetch_commands()
    assert [item.command_id for item in commands] == ["cmd-recover"]
    ref = transport._comment_refs["cmd-recover"]
    assert ref["channel_id"] == "chat-a"
    assert ref["generation"] == "gen-a"
