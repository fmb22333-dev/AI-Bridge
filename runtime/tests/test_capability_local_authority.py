from types import SimpleNamespace

from ai_bridge.core.sessions import SessionInfo, SessionRegistry
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


class _Service:
    def __init__(self, *, adapter_version="0.5.26"):
        self.sessions = SessionRegistry(stale_after_seconds=3600)
        self.calls = 0
        self.sessions.register(SessionInfo(
            session_id="HOU-LOCAL", adapter="houdini", adapter_version=adapter_version,
            host_version="21.0.440", pid=123, project_file="E:/test.hip",
            capabilities=(
                CapabilityDescriptor(name="geometry.query", version="1.0", write=False, host_mutation=False, risk=RiskLevel.L1),
                CapabilityDescriptor(name="capability.search", version="1.0", write=False, host_mutation=False, risk=RiskLevel.L1),
            ),
        ))

    def execute(self, command):
        self.calls += 1
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={
            "source": "host", "integrity": {"ok": True}, "results": [],
            "full_host_evidence": {"schema": {"x": 1}, "guidance": {"y": 2}},
        })


def _command(command_id, *, query="geometry.query", detail=None, force_host=False):
    arguments = {"query": query, "limit": 6}
    if detail is not None: arguments["detail"] = detail
    if force_host: arguments["force_host"] = True
    return CommandEnvelope.model_validate({
        "protocol": "bridge/1", "command_id": command_id, "workspace": "Houdini",
        "adapter": "houdini", "operation": "capability.search", "session": "HOU-LOCAL",
        "project_file": "E:/test.hip", "arguments": arguments,
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False}, "risk": "L1",
    })


def _runner(*, adapter_version="0.5.27"):
    service = _Service(adapter_version=adapter_version)
    return service, TransportRunner(service, SimpleNamespace())


def _geometry_row(result):
    return next(item for item in result.result["results"] if item.get("name") == "geometry.query")


def test_default_capability_search_uses_runtime_local_authority_without_host_call():
    service, runner = _runner()
    result = runner._execute_with_capability_cache(_command("cmd-local", query="network.ensure_and_cook"))
    assert service.calls == 0
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["results"]
    assert result.result["_bridge"]["capability_authority"]["mode"] == "runtime_local"


def test_detail_full_keeps_complete_authority_fields_locally():
    service, runner = _runner()
    result = runner._execute_with_capability_cache(_command("cmd-full", detail="full"))
    assert service.calls == 0
    row = _geometry_row(result)
    assert row["argument_schema"]["required"]["path"] == "str"
    assert "verification" in row
    assert "guidance" in row
    assert "related_knowledge" in result.result


def test_compact_then_full_never_loses_complete_authority_fields():
    service, runner = _runner()
    first = runner._execute_with_capability_cache(_command("cmd-compact-first"))
    second = runner._execute_with_capability_cache(_command("cmd-full-second", detail="full"))
    assert service.calls == 0
    assert first.result["_bridge"]["capability_authority"]["mode"] == "runtime_local"
    assert second.result["_bridge"]["capability_authority"]["mode"] == "runtime_local"
    row = _geometry_row(second)
    assert row["argument_schema"]["required"]["path"] == "str"
    assert "verification" in row and "guidance" in row
    assert "related_knowledge" in second.result


def test_force_host_always_bypasses_local_authority_and_cache():
    service, runner = _runner()
    runner._execute_with_capability_cache(_command("cmd-warm-local"))
    first = runner._execute_with_capability_cache(_command("cmd-force-1", force_host=True))
    second = runner._execute_with_capability_cache(_command("cmd-force-2", detail="full", force_host=True))
    assert service.calls == 2
    assert first.result["source"] == "host" and second.result["source"] == "host"
    assert second.result["full_host_evidence"]["schema"]["x"] == 1
    assert second.result["_bridge"]["capability_authority"]["mode"] == "host_forced"


def test_adapter_version_mismatch_falls_back_to_host_instead_of_using_local_authority():
    service, runner = _runner(adapter_version="0.5.999")
    result = runner._execute_with_capability_cache(_command("cmd-mismatch"))
    assert service.calls == 1
    assert result.result["source"] == "host"
    assert result.result["_bridge"]["capability_authority"]["mode"] == "host_fallback"
