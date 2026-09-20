from types import SimpleNamespace

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.result_delivery import prepare_result_delivery


class _Transport:
    def __init__(self):
        self.config = SimpleNamespace(bridge_id="FMB-test")
        self._comment_refs = {"cmd-cap": {"mode": "issue_channel_v5", "channel_id": "c", "generation": "g"}}
        self.published = []

    def _bus_path(self, path):
        return ".ai-bridge/" + path

    def _publish_json(self, *args, **kwargs):
        self.published.append((args, kwargs))


def test_compact_delivery_preserves_capability_authority_and_cache_metadata():
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1", "command_id": "cmd-cap", "workspace": "Houdini",
        "adapter": "houdini", "operation": "capability.search",
        "arguments": {"query": "geometry.query"},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False}, "risk": "L1",
    })
    result = ExecutionResult(command_id="cmd-cap", status=ExecutionStatus.SUCCESS, result={
        "query": "geometry.query", "results": [], "integrity": {"ok": True},
        "_bridge": {
            "capability_authority": {"mode": "runtime_local", "catalog_digest": "abc"},
            "capability_cache": {"hit": False, "catalog_digest": "abc"},
        },
    })
    delivered = prepare_result_delivery(_Transport(), command, result)
    assert delivered.result["_bridge"]["delivery"]["mode"] == "capability_compact"
    assert delivered.result["_bridge"]["capability_authority"]["mode"] == "runtime_local"
    assert delivered.result["_bridge"]["capability_cache"]["hit"] is False
