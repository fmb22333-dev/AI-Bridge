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


def test_message_state_reports_multi_ingress_only_when_both_inputs_are_active():
    transport = _transport()

    transport._message_mode = "issue_channel_v5"
    state = transport.message_state()
    assert state["multi_ingress"] is True
    assert state["primary_ingress"] == "issue_comment_v5"
    assert state["fallback_ingress"] == "contents"

    transport._message_mode = "contents"
    state = transport.message_state()
    assert state["multi_ingress"] is False
    assert state["active_ingress"] == "contents"
    assert state["fallback_ingress"] == "contents"

    transport._message_mode = "issue_mailbox_v3"
    state = transport.message_state()
    assert state["multi_ingress"] is False
    assert state["active_ingress"] == "issue_mailbox_v3"
