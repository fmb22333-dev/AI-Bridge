from types import SimpleNamespace

from ai_bridge.core.sessions import SessionInfo, SessionRegistry
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.transport.result_delivery import prepare_result_delivery


class _Workspaces:
    def list(self):
        return [SimpleNamespace(workspace_id="Bridge")]


class _SecretStore:
    def get(self, _key):
        return None


class _PresenceTransport:
    def __init__(self):
        self.state = {
            "mode": "issue_channel_v5",
            "channel_protocol": "v5",
            "multi_channel": True,
            "multi_ingress": True,
            "primary_ingress": "issue_comment_v5",
            "fallback_ingress": "contents",
            "comment_ingress_detail": "available",
            "contents_ingress_detail": "available",
            "comment_write_ok": True,
        }
        self.published = []

    def message_state(self):
        return dict(self.state)

    def publish_presence(self, payload):
        self.published.append(dict(payload))


def _controller(tmp_path, capabilities=None):
    sessions = SessionRegistry(stale_after_seconds=3600)
    if capabilities is not None:
        sessions.register(
            SessionInfo(
                session_id="HOU-TEST",
                adapter="houdini",
                adapter_version="0.5.26",
                host_version="21.0.440",
                pid=123,
                project_file="E:/test.hip",
                capabilities=tuple(capabilities),
            )
        )
    service = SimpleNamespace(
        sessions=sessions,
        workspaces=_Workspaces(),
        emergency_stop=SimpleNamespace(write_blocked=False),
    )
    return RemoteController(
        service=service,
        data_dir=tmp_path,
        runtime_state={},
        secret_store=_SecretStore(),
    )


def test_presence_ignores_volatile_transport_details(tmp_path):
    controller = _controller(tmp_path)
    transport = _PresenceTransport()
    config = SimpleNamespace(bridge_id="FMB-test")

    controller._publish_presence_if_changed(transport, config, force=True)
    transport.state["comment_ingress_detail"] = "available: not_modified"
    transport.state["contents_ingress_detail"] = "not_polled"
    controller._publish_presence_if_changed(transport, config)

    assert len(transport.published) == 1


def test_presence_publishes_session_identity_and_capability_digest(tmp_path):
    caps = [
        CapabilityDescriptor(name="z.op", version="1.0", write=False, host_mutation=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="a.op", version="2.0", write=True, host_mutation=False, risk=RiskLevel.L2),
    ]
    controller = _controller(tmp_path, caps)
    core = controller._presence_core("FMB-test", _PresenceTransport())
    session = core["sessions"][0]

    assert session["adapter_version"] == "0.5.26"
    assert session["plugin_version"] == "0.5.26"
    assert session["build_sha"] is None
    assert len(session["capability_digest"]) == 64


def test_capability_search_defaults_to_compact_comment_delivery_without_git_archive():
    class Transport:
        def __init__(self):
            self.config = SimpleNamespace(bridge_id="FMB-test")
            self._comment_refs = {
                "cmd-cap": {"mode": "issue_channel_v5", "channel_id": "c", "generation": "g"}
            }
            self.published = []

        def _bus_path(self, path):
            return ".ai-bridge/" + path

        def _publish_json(self, *args, **kwargs):
            self.published.append((args, kwargs))

    transport = Transport()
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": "cmd-cap",
        "workspace": "Houdini",
        "adapter": "houdini",
        "operation": "capability.search",
        "arguments": {"query": "network cook"},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })
    noisy = {
        "kind": "recipe",
        "name": "network.ensure_and_cook",
        "promotion_state": "promoted",
        "execution_authorized": True,
        "version": "1.1",
        "description": "desired state network reconcile and cook",
        "required": ["parent", "nodes"],
        "optional": ["connections"],
        "promotion": {"evidence": ["x" * 5000], "state_history": ["y" * 5000]},
        "guidance": {"details": "z" * 5000},
        "_search": {"score": 1000},
    }
    result = ExecutionResult(
        command_id="cmd-cap",
        status=ExecutionStatus.SUCCESS,
        result={
            "query": "network cook",
            "count": 1,
            "total_matches": 1,
            "results": [noisy],
            "related_knowledge": [{"content": "k" * 5000}],
            "adapter": "houdini",
            "adapter_version": "0.5.26",
            "host_version": "21.0.440",
            "integrity": {"ok": True, "issue_count": 0},
        },
    )

    compact = prepare_result_delivery(transport, command, result)

    assert transport.published == []
    assert compact.result["_bridge"]["delivery"]["mode"] == "capability_compact"
    row = compact.result["results"][0]
    assert row["name"] == "network.ensure_and_cook"
    assert row["state"] == "promoted"
    assert row["required"] == ["parent", "nodes"]
    assert "promotion" not in row
    assert "guidance" not in row
    assert "related_knowledge" not in compact.result


def test_capability_search_full_detail_preserves_full_semantics_without_normal_archive():
    class Transport:
        def __init__(self):
            self.config = SimpleNamespace(bridge_id="FMB-test")
            self._comment_refs = {
                "cmd-cap-full": {"mode": "issue_channel_v5", "channel_id": "c", "generation": "g"}
            }
            self.published = []

        def _bus_path(self, path):
            return ".ai-bridge/" + path

        def _publish_json(self, *args, **kwargs):
            self.published.append((args, kwargs))

    transport = Transport()
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": "cmd-cap-full",
        "workspace": "Houdini",
        "adapter": "houdini",
        "operation": "capability.search",
        "arguments": {"query": "network cook", "detail": "full"},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })
    result = ExecutionResult(
        command_id="cmd-cap-full",
        status=ExecutionStatus.SUCCESS,
        result={"results": [{"name": "x", "huge": "x" * 20000}]},
    )

    delivered = prepare_result_delivery(transport, command, result)

    assert transport.published == []
    assert delivered is result
    assert delivered.result["results"][0]["huge"] == "x" * 20000
