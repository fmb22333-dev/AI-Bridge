from types import SimpleNamespace

from ai_bridge.security.secret_store import MemorySecretStore
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.transport.supabase_bus import SupabaseBusConfig, SupabaseBusTransport
import ai_bridge.transport.supabase_extension  # noqa: F401


def _service():
    return SimpleNamespace(
        db=SimpleNamespace(),
        sessions=SimpleNamespace(list=lambda: []),
        emergency_stop=SimpleNamespace(write_blocked=False),
        workspaces=SimpleNamespace(list=lambda: []),
    )


class _GitHubV5State:
    def message_state(self):
        return {
            "mode": "issue_channel_v5",
            "channel_protocol": "v5",
            "multi_channel": True,
            "multi_ingress": True,
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


def test_supabase_transport_identifies_itself_as_primary_realtime_transport():
    transport = SupabaseBusTransport(
        SupabaseBusConfig(
            project_url="https://example.supabase.co",
            secret_key="sb_secret_test",
            bridge_id="bridge-test",
        ),
        client=object(),
    )
    state = transport.message_state()
    assert state["mode"] == "supabase_primary"
    assert state["kind"] == "supabase_primary"
    assert state["role"] == "primary_realtime_command_transport"


def test_presence_is_unambiguous_without_reading_external_docs(tmp_path):
    runtime_state = {}
    remote = RemoteController(
        service=_service(),
        data_dir=tmp_path,
        runtime_state=runtime_state,
        secret_store=MemorySecretStore(),
    )
    runtime_state["fallback_transport"] = {
        "configured": True,
        "status": "connected",
        "kind": "supabase_fallback",
        "role": "primary_fast",
        "project_url": "https://example.supabase.co",
        "bridge_id": "bridge-live",
        "table": "ai_bridge_commands",
    }

    presence = remote._presence_core("bridge-live", _GitHubV5State())

    assert presence["realtime_command_primary"] == "supabase"
    assert presence["realtime_command_fallback"] == "github_v5"
    assert presence["durable_authority"] == "github"

    supabase = presence["supabase_transport"]
    assert supabase["kind"] == "supabase_primary"
    assert supabase["role"] == "primary_realtime_command_transport"

    github = presence["message_transport"]
    assert github["role"] == "fallback_command_transport"
    assert github["github_primary_ingress"] == "issue_comment_v5"
    assert github["github_fallback_ingress"] == "contents"
    assert "primary_ingress" not in github
    assert "fallback_ingress" not in github
    assert github["compatibility"]["deprecated_fields"]["primary_ingress"] == "issue_comment_v5"

    assert "command_transport_policy" not in presence
    assert "fallback_transport" not in presence
    deprecated = presence["compatibility"]["deprecated_transport_fields"]
    assert deprecated["command_transport_policy"] == {
        "primary": "supabase",
        "fallback": "github_v5",
        "authority": "github",
    }
    assert deprecated["fallback_transport"]["kind"] == "supabase_fallback"
