import httpx

import ai_bridge.transport.github_bus as gb


def _transport():
    return gb.GitHubBusTransport(
        gb.GitHubBusConfig(
            repository="owner/repo",
            token="x",
            bridge_id="bridge-test",
        ),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
    )


def test_message_state_scopes_github_ingress_names_and_keeps_legacy_only_in_compatibility():
    transport = _transport()

    transport._message_mode = "issue_channel_v5"
    state = transport.message_state()
    assert state["role"] == "fallback_command_transport"
    assert state["multi_ingress"] is True
    assert state["github_primary_ingress"] == "issue_comment_v5"
    assert state["github_fallback_ingress"] == "contents"
    assert "primary_ingress" not in state
    assert "fallback_ingress" not in state
    assert state["compatibility"]["deprecated_fields"] == {
        "primary_ingress": "issue_comment_v5",
        "fallback_ingress": "contents",
    }

    transport._message_mode = "contents"
    state = transport.message_state()
    assert state["role"] == "fallback_command_transport"
    assert state["multi_ingress"] is False
    assert state["github_active_ingress"] == "contents"
    assert state["github_fallback_ingress"] == "contents"
    assert "active_ingress" not in state
    assert "fallback_ingress" not in state

    transport._message_mode = "issue_mailbox_v3"
    state = transport.message_state()
    assert state["role"] == "fallback_command_transport"
    assert state["multi_ingress"] is False
    assert state["github_active_ingress"] == "issue_mailbox_v3"
    assert "active_ingress" not in state
