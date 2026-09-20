from ai_bridge.transport.remote_controller import RemoteController


class _GitHubState:
    def message_state(self):
        return {
            "mode": "issue_channel_v5",
            "role": "fallback_command_transport",
            "github_primary_ingress": "issue_comment_v5",
            "github_fallback_ingress": "contents",
            "compatibility": {
                "deprecated_fields": {
                    "primary_ingress": "issue_comment_v5",
                    "fallback_ingress": "contents",
                }
            },
        }


def test_presence_transport_filter_preserves_unambiguous_github_state():
    state = RemoteController._presence_transport_state(_GitHubState())
    assert state["role"] == "fallback_command_transport"
    assert state["github_primary_ingress"] == "issue_comment_v5"
    assert state["github_fallback_ingress"] == "contents"
    assert state["compatibility"]["deprecated_fields"]["primary_ingress"] == "issue_comment_v5"
    assert "primary_ingress" not in state
    assert "fallback_ingress" not in state
