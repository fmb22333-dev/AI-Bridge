from types import SimpleNamespace

from ai_bridge.core.sessions import SessionInfo, SessionRegistry
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


class _Service:
    def __init__(self):
        self.sessions = SessionRegistry(stale_after_seconds=3600)
        self.calls = 0

    def execute(self, command):
        self.calls += 1
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={
                "query": command.arguments.get("query"),
                "integrity": {"ok": True},
                "results": [{"kind": "capability", "name": "network.ensure"}],
            },
        )


def _cap(name="node.inspect"):
    return CapabilityDescriptor(
        name=name, version="1.0", write=False, host_mutation=False, risk=RiskLevel.L1
    )


def _command(command_id, *, query="network cook", detail=None):
    arguments = {"query": query, "limit": 6}
    if detail is not None:
        arguments["detail"] = detail
    return CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Houdini",
        "adapter": "houdini",
        "operation": "capability.search",
        "session": "HOU-CACHE",
        "project_file": "E:/test.hip",
        "arguments": arguments,
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })


def _runner():
    service = _Service()
    service.sessions.register(SessionInfo(
        session_id="HOU-CACHE", adapter="houdini", adapter_version="0.5.999",
        host_version="21.0.440", pid=123, project_file="E:/test.hip",
        capabilities=(_cap(),),
    ))
    runner = TransportRunner(service, SimpleNamespace())
    return service, runner


def test_repeated_capability_search_reuses_success_for_same_session_catalog():
    service, runner = _runner()

    first = runner._execute_with_capability_cache(_command("cmd-1"))
    second = runner._execute_with_capability_cache(_command("cmd-2"))

    assert service.calls == 1
    assert first.command_id == "cmd-1"
    assert second.command_id == "cmd-2"
    assert second.result["_bridge"]["capability_cache"]["hit"] is True
    assert second.result["_bridge"]["capability_cache"]["source_command_id"] == "cmd-1"


def test_capability_cache_invalidates_when_session_capability_digest_changes():
    service, runner = _runner()
    runner._execute_with_capability_cache(_command("cmd-1"))

    service.sessions.register(SessionInfo(
        session_id="HOU-CACHE", adapter="houdini", adapter_version="0.5.999",
        host_version="21.0.440", pid=123, project_file="E:/test.hip",
        capabilities=(_cap(), _cap("geometry.query")),
    ))
    result = runner._execute_with_capability_cache(_command("cmd-2"))

    assert service.calls == 2
    assert result.result["_bridge"]["capability_cache"]["hit"] is False


def test_failed_or_unknown_capability_search_is_not_cached():
    service, runner = _runner()

    def fail(command):
        service.calls += 1
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.UNKNOWN, result={})

    service.execute = fail
    runner._execute_with_capability_cache(_command("cmd-1"))
    runner._execute_with_capability_cache(_command("cmd-2"))

    assert service.calls == 2
