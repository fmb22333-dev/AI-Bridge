from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.remote_adapter import QueueAdapterExecutor
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


def _command(command_id: str, *, project_file: str = "E:/AA/project.hip", budget_seconds=None, auto_recover=None):
    execution = {"verify": True, "checkpoint": "auto", "dry_run": False}
    if budget_seconds is not None:
        execution["budget_seconds"] = budget_seconds
    if auto_recover is not None:
        execution["auto_recover"] = auto_recover
    return CommandEnvelope.model_validate({
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-BUDGET",
        "project_file": project_file,
        "operation": "inspect.context",
        "arguments": {},
        "execution": execution,
        "risk": "L1",
    })


def test_budget_precedence_explicit_then_project_then_bridge_default(tmp_path):
    store = ExecutionPolicyStore(
        tmp_path / "execution_policy.json",
        default_budget_seconds=120,
        default_auto_recover=False,
    )
    store.set_project("E:/AA/project.hip", 45)
    store.set_workspace("Bridge", 80, auto_recover=True)

    explicit = store.resolve(_command("c-explicit", budget_seconds=9))
    assert explicit.seconds == 9
    assert explicit.source == "ai_explicit"
    assert explicit.auto_recover is True
    assert explicit.auto_recover_source == "workspace_default"

    project = store.resolve(_command("c-project"))
    assert project.seconds == 45
    assert project.source == "project_default"
    assert project.auto_recover is True

    workspace = store.resolve(_command("c-workspace", project_file="E:/AA/other.hip"))
    assert workspace.seconds == 80
    assert workspace.source == "workspace_default"
    assert workspace.auto_recover is True

    other_workspace = _command("c-default", project_file="E:/AA/other.hip").model_copy(deep=True)
    other_workspace.workspace = "Other"
    fallback = store.resolve(other_workspace)
    assert fallback.seconds == 120
    assert fallback.source == "bridge_default"
    assert fallback.auto_recover is False


def test_budget_policy_persists_project_override(tmp_path):
    path = tmp_path / "execution_policy.json"
    first = ExecutionPolicyStore(path, default_budget_seconds=120)
    first.set_default(180, auto_recover=True)
    first.set_project("E:/AA/project.hip", 75)
    first.set_workspace("Bridge", 90, auto_recover=False)

    second = ExecutionPolicyStore(path)
    assert second.default_budget_seconds == 180
    assert second.default_auto_recover is True
    assert second.project_policy("E:/AA/project.hip")["budget_seconds"] == 75
    assert second.workspace_policy("Bridge")["budget_seconds"] == 90
    assert second.workspace_policy("Bridge")["auto_recover"] is False


class FakeBus:
    def __init__(self):
        self.timeout = None

    def submit(self, session_id, command, *, timeout):
        self.timeout = timeout
        return None


def test_queue_executor_reports_budget_exceeded_instead_of_fixed_adapter_timeout():
    bus = FakeBus()
    executor = QueueAdapterExecutor(bus)
    result = executor.execute(_command("c-timeout"), timeout=120)

    assert bus.timeout == 120
    assert result.status == ExecutionStatus.UNKNOWN
    assert result.failure.code == "EXECUTION_BUDGET_EXCEEDED"
    assert result.failure.retryable is False
    assert result.last_known_state["budget_seconds"] == 120
    assert result.last_known_state["host_operation_may_still_be_running"] is True


class RecordingQueueExecutor(QueueAdapterExecutor):
    def __init__(self):
        self.seen = []

    def execute(self, command, *, timeout=None):
        self.seen.append((command.command_id, timeout))
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )


def _service(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", workspace_root)

    policy = ExecutionPolicyStore(tmp_path / "execution_policy.json", default_budget_seconds=120)
    policy.set_project("E:/AA/project.hip", 44)

    service = BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
        execution_policy=policy,
    )
    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-BUDGET",
        "adapter": "houdini",
        "adapter_version": "0.5.13",
        "host_version": "21.0.440",
        "pid": 1234,
        "project_file": "E:/AA/project.hip",
        "capabilities": [{
            "name": "inspect.context",
            "version": "1.0",
            "write": False,
            "risk": "L1",
            "rollback": False,
            "verification": True,
            "tested_host_versions": ["21.0"],
        }],
    }))
    executor = RecordingQueueExecutor()
    service.register_executor("houdini", executor)
    return service, executor


def test_service_passes_resolved_project_budget_and_records_source(tmp_path):
    service, executor = _service(tmp_path)

    result = service.execute(_command("c-service-project"))

    assert executor.seen == [("c-service-project", 44)]
    budget = result.result["_bridge"]["execution_budget"]
    assert budget["seconds"] == 44
    assert budget["source"] == "project_default"


def test_service_explicit_ai_budget_overrides_project_default(tmp_path):
    service, executor = _service(tmp_path)

    result = service.execute(_command("c-service-ai", budget_seconds=11))

    assert executor.seen == [("c-service-ai", 11)]
    budget = result.result["_bridge"]["execution_budget"]
    assert budget["seconds"] == 11
    assert budget["source"] == "ai_explicit"



def test_auto_recovery_policy_inherits_workspace_and_ai_can_override(tmp_path):
    store = ExecutionPolicyStore(tmp_path / "execution_policy.json", default_budget_seconds=120)
    store.set_default(120, auto_recover=False)
    store.set_workspace("Bridge", 80, auto_recover=True)

    inherited = store.resolve(_command("c-recovery-inherit", project_file="E:/AA/other.hip"))
    assert inherited.auto_recover is True
    assert inherited.auto_recover_source == "workspace_default"

    explicit_off = _command("c-recovery-off", project_file="E:/AA/other.hip").model_copy(deep=True)
    explicit_off.execution.auto_recover = False
    resolved = store.resolve(explicit_off)
    assert resolved.auto_recover is False
    assert resolved.auto_recover_source == "ai_explicit"


def test_numeric_workspace_policy_migrates_from_runtime_0267(tmp_path):
    import json

    path = tmp_path / "execution_policy.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "default_budget_seconds": 120,
        "projects": {},
        "workspaces": {"Bridge": 77},
    }), encoding="utf-8")

    store = ExecutionPolicyStore(path)
    item = store.workspace_policy("Bridge")
    assert item["budget_seconds"] == 77
    assert item["auto_recover"] is None


def test_ai_can_override_auto_recovery_without_changing_budget_source(tmp_path):
    store = ExecutionPolicyStore(
        tmp_path / "execution_policy.json",
        default_budget_seconds=120,
        default_auto_recover=True,
    )
    store.set_workspace("Bridge", 80, auto_recover=True)

    resolved = store.resolve(
        _command("c-ai-recovery-off", auto_recover=False)
    )

    assert resolved.seconds == 80
    assert resolved.source == "workspace_default"
    assert resolved.auto_recover is False
    assert resolved.auto_recover_source == "ai_explicit"
