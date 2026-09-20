import json

import httpx

import ai_bridge.transport.github_bus as gb


def _command(command_id="cmd-invalid-ingress"):
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    }


def _transport(state):
    state.setdefault(
        100,
        gb.MAILBOX_MARKER_V3
        + "\n"
        + json.dumps({"bridge_id": "bridge-test", "state": "idle"}),
    )

    def item(comment_id):
        return {
            "id": comment_id,
            "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
            "body": state[comment_id],
        }

    def handler(request):
        headers = {"ETag": '"invalid-ingress-v1"'}
        path = request.url.path
        if request.method == "GET" and path.endswith("/issues/comments"):
            return httpx.Response(200, headers=headers, json=[item(comment_id) for comment_id in state])
        if request.method == "GET" and path.endswith("/issues/1/comments"):
            return httpx.Response(200, headers=headers, json=[item(comment_id) for comment_id in state])
        if request.method == "GET" and "/issues/comments/" in path:
            return httpx.Response(200, headers=headers, json=item(int(path.rsplit("/", 1)[-1])))
        if request.method == "PATCH" and "/issues/comments/" in path:
            comment_id = int(path.rsplit("/", 1)[-1])
            state[comment_id] = json.loads(request.content.decode())["body"]
            return httpx.Response(200, headers=headers, json=item(comment_id))
        if request.method == "GET" and "/contents/.ai-bridge/commands/" in path:
            return httpx.Response(404, headers=headers, json={"message": "Not Found"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return gb.GitHubBusTransport(
        gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _payload(body):
    assert body.startswith("AI_BRIDGE_NACK_V5\n")
    return json.loads(body.split("\n", 1)[1])


def test_unsupported_command_marker_is_nacked_instead_of_silently_ignored():
    state = {
        201: "AI_BRIDGE_COMMAND_V1\n"
        + json.dumps(
            {
                "bridge_id": "bridge-test",
                "channel_id": "chat-old",
                "generation": "gen-old",
                "command": _command("cmd-old"),
            }
        )
    }
    transport = _transport(state)
    transport.initialize_message_mode()

    assert transport.fetch_commands() == []

    nack = _payload(state[201])
    assert nack["error"]["code"] == "UNSUPPORTED_COMMAND_MARKER"
    assert nack["error"]["received_marker"] == "AI_BRIDGE_COMMAND_V1"
    assert nack["error"]["expected_marker"] == "AI_BRIDGE_COMMAND_V5"
    assert nack["command_id"] == "cmd-old"


def test_malformed_v5_json_is_nacked_instead_of_silently_ignored():
    state = {201: 'AI_BRIDGE_COMMAND_V5\n{"bridge_id":"bridge-test","channel_id":'}
    transport = _transport(state)
    transport.initialize_message_mode()

    assert transport.fetch_commands() == []

    nack = _payload(state[201])
    assert nack["error"]["code"] == "INVALID_COMMAND_ENVELOPE"
    assert nack["error"]["expected_marker"] == "AI_BRIDGE_COMMAND_V5"


def test_v5_missing_bridge_id_is_nacked_with_field_error():
    state = {
        201: "AI_BRIDGE_COMMAND_V5\n"
        + json.dumps(
            {
                "channel_id": "chat-missing-target",
                "generation": "gen-missing-target",
                "command": _command("cmd-missing-target"),
            }
        )
    }
    transport = _transport(state)
    transport.initialize_message_mode()

    assert transport.fetch_commands() == []

    nack = _payload(state[201])
    assert nack["error"]["code"] == "INVALID_COMMAND_ENVELOPE"
    assert "bridge_id" in nack["error"]["message"]


def test_command_explicitly_targeting_another_bridge_is_ignored():
    state = {
        201: "AI_BRIDGE_COMMAND_V1\n"
        + json.dumps(
            {
                "bridge_id": "another-bridge",
                "channel_id": "chat-other",
                "generation": "gen-other",
                "command": _command("cmd-other"),
            }
        )
    }
    transport = _transport(state)
    original = state[201]
    transport.initialize_message_mode()

    assert transport.fetch_commands() == []
    assert state[201] == original
