from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus

PROJECT = "E:/UnrealProjects/Test/Test.uproject"

class RecordingExecutor:
    def __init__(self):
        self.calls = []
    def execute(self, command):
        self.calls.append(command.operation)
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={"verified": True})

def _service(tmp_path, *, auto_recover=False):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("UE", workspace_root)
    policy = ExecutionPolicyStore(tmp_path / "execution_policy.json", default_budget_seconds=120, default_auto_recover=False)
    policy.set_workspace("UE", 30, auto_recover=auto_recover)
    return BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=workspaces, execution_policy=policy)

def _register_exporter(service, *, host_mutation_marker="explicit_false", long_running=False):
    capability = {"name": "export.external_payload", "version": "1.0", "write": True, "risk": "L2", "rollback": False, "verification": True, "long_running": long_running, "tested_host_versions": ["5.6"]}
    if host_mutation_marker == "explicit_false":
        capability["host_mutation"] = False
    service.register_adapter_session(SessionRegistration.model_validate({"session_id": "UE-TEST", "adapter": "unreal", "adapter_version": "0.5.0", "host_version": "5.6.1", "pid": 1234, "project_file": PROJECT, "capabilities": [capability]}))

def _command(command_id):
    return CommandEnvelope.model_validate({"command_id": command_id, "workspace": "UE", "adapter": "unreal", "session": "UE-TEST", "project_file": PROJECT, "operation": "export.external_payload", "arguments": {}, "execution": {"verify": True, "checkpoint": "auto", "dry_run": False}, "risk": "L2"})

def test_external_l2_write_without_host_mutation_dispatches_without_checkpoint(tmp_path):
    service = _service(tmp_path)
    _register_exporter(service, host_mutation_marker="explicit_false")
    executor = RecordingExecutor()
    service.register_executor("unreal", executor)
    result = service.execute(_command("external-write"))
    assert result.status == ExecutionStatus.SUCCESS
    assert executor.calls == ["export.external_payload"]
    assert "checkpoint_id" not in result.result.get("_bridge", {})

def test_l2_write_defaults_to_host_mutation_and_still_requires_checkpoint(tmp_path):
    service = _service(tmp_path)
    _register_exporter(service, host_mutation_marker="implicit_default")
    executor = RecordingExecutor()
    service.register_executor("unreal", executor)
    result = service.execute(_command("default-host-write"))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure is not None
    assert result.failure.code == "CHECKPOINT_CAPABILITY_REQUIRED"
    assert executor.calls == []

def test_external_write_is_still_blocked_by_emergency_stop(tmp_path):
    service = _service(tmp_path)
    _register_exporter(service, host_mutation_marker="explicit_false")
    executor = RecordingExecutor()
    service.register_executor("unreal", executor)
    service.stop_writes()
    result = service.execute(_command("external-write-stopped"))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure is not None
    assert result.failure.code == "EMERGENCY_STOP"
    assert executor.calls == []

def test_long_running_external_write_still_requires_checkpoint_for_auto_recovery(tmp_path):
    service = _service(tmp_path, auto_recover=True)
    _register_exporter(service, host_mutation_marker="explicit_false", long_running=True)
    executor = RecordingExecutor()
    service.register_executor("unreal", executor)
    result = service.execute(_command("external-long-running"))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure is not None
    assert result.failure.code == "CHECKPOINT_CAPABILITY_REQUIRED"
    assert executor.calls == []
