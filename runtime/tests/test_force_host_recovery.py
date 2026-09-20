from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters.bridge_admin import descriptor as bridge_admin_descriptor
from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.remote_adapter import QueueAdapterExecutor
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import (
    ExecutionResult,
    ExecutionStatus,
    FailureInfo,
    FailureOrigin,
)


class FakeHostProcess:
    def __init__(self, *, running=True):
        self.calls = []
        self.running = bool(running)

    def is_running(self, pid):
        return self.running

    def executable_path(self, pid):
        self.calls.append(("executable_path", int(pid)))
        return "C:/Program Files/Side Effects Software/Houdini/bin/houdinifx.exe"

    def terminate(self, pid, *, force_after_seconds=1.0):
        self.calls.append(("terminate", int(pid)))
        self.running = False
        return {"pid": int(pid), "terminated": True, "elapsed_ms": 1.0}

    def launch(self, executable, project_file):
        self.calls.append(("launch", executable, project_file))
        return {"pid": 222, "executable": executable, "project_file": project_file}


def make_service(tmp_path, *, budget=7, auto_recover=True, host_process=None):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", workspace_root)
    policy = ExecutionPolicyStore(
        tmp_path / "execution_policy.json",
        default_budget_seconds=120,
        default_auto_recover=False,
    )
    policy.set_workspace("Bridge", budget, auto_recover=auto_recover)
    return BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
        execution_policy=policy,
        host_process=host_process or FakeHostProcess(),
    )


def register_houdini(service, project_file, capabilities):
    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-TEST",
        "adapter": "houdini",
        "adapter_version": "0.5.16",
        "host_version": "21.0.440",
        "pid": 111,
        "project_file": project_file,
        "capabilities": capabilities,
    }))


class CheckpointTimeoutExecutor(QueueAdapterExecutor):
    def __init__(self):
        self.calls = []

    def execute(self, command, *, timeout=None):
        self.calls.append((command.operation, timeout))
        if command.operation == "checkpoint.create":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.UNKNOWN,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER,
                    stage="execution_budget",
                    code="EXECUTION_BUDGET_EXCEEDED",
                    message="checkpoint did not return",
                    retryable=False,
                ),
                last_known_state={"host_operation_may_still_be_running": True},
            )
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )


def test_precheckpoint_wait_is_bounded_by_command_budget_and_escalates(tmp_path, monkeypatch):
    project = str(tmp_path / "project.hip")
    service = make_service(tmp_path, budget=7, auto_recover=True)
    register_houdini(service, project, [
        {
            "name": "checkpoint.create",
            "version": "1.0",
            "write": True,
            "risk": "L1",
            "rollback": False,
            "verification": True,
            "tested_host_versions": ["21.0"],
        },
        {
            "name": "cook.execute",
            "version": "1.0",
            "write": False,
            "risk": "L1",
            "rollback": False,
            "verification": True,
            "long_running": True,
            "tested_host_versions": ["21.0"],
        },
    ])
    executor = CheckpointTimeoutExecutor()
    service.register_executor("houdini", executor)

    recovery_calls = []

    def fake_force_recover_host(**kwargs):
        recovery_calls.append(kwargs)
        return {
            "status": "recovered",
            "recovery_id": "force-test",
            "new_session_id": "HOU-NEW",
            "new_pid": 222,
        }

    monkeypatch.setattr(service, "force_recover_host", fake_force_recover_host)

    command = CommandEnvelope.model_validate({
        "command_id": "budget-checkpoint-timeout",
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-TEST",
        "project_file": project,
        "operation": "cook.execute",
        "arguments": {"path": "/obj/test"},
        "execution": {
            "verify": True,
            "checkpoint": "auto",
            "dry_run": False,
            "budget_seconds": 7,
            "auto_recover": True,
        },
        "risk": "L1",
    })

    result = service.execute(command)

    assert executor.calls == [("checkpoint.create", 7.0)]
    assert len(recovery_calls) == 1
    assert recovery_calls[0]["session_id"] == "HOU-TEST"
    assert recovery_calls[0]["allow_saved_project_fallback"] is False
    assert result.failure.code == "EXECUTION_BUDGET_EXCEEDED"
    assert result.result["_bridge"]["recovery"]["status"] == "recovered"
    assert result.last_known_state["new_session_id"] == "HOU-NEW"


class RecordingQueueExecutor(QueueAdapterExecutor):
    def __init__(self):
        self.calls = []

    def execute(self, command, *, timeout=None):
        self.calls.append((command.operation, timeout))
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )


def test_rollback_execute_never_creates_recursive_checkpoint(tmp_path):
    project = str(tmp_path / "project.hip")
    service = make_service(tmp_path, budget=11, auto_recover=False)
    register_houdini(service, project, [
        {
            "name": "checkpoint.create",
            "version": "1.0",
            "write": True,
            "risk": "L1",
            "rollback": False,
            "verification": True,
            "tested_host_versions": ["21.0"],
        },
        {
            "name": "rollback.execute",
            "version": "1.0",
            "write": True,
            "risk": "L2",
            "rollback": False,
            "verification": True,
            "tested_host_versions": ["21.0"],
        },
    ])
    executor = RecordingQueueExecutor()
    service.register_executor("houdini", executor)

    command = CommandEnvelope.model_validate({
        "command_id": "rollback-no-recursive-checkpoint",
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-TEST",
        "project_file": project,
        "operation": "rollback.execute",
        "arguments": {
            "checkpoint_path": str(tmp_path / "backup.hip"),
            "original_hip": project,
        },
        "execution": {
            "verify": True,
            "checkpoint": "none",
            "dry_run": False,
            "budget_seconds": 11,
            "auto_recover": False,
        },
        "risk": "L2",
    })

    result = service.execute(command)

    assert result.status == ExecutionStatus.SUCCESS
    assert executor.calls == [("rollback.execute", 11.0)]


class DirectRecoveryExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, command):
        self.calls.append(command)
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )


def test_force_recovery_can_target_pid_and_project_without_session_registry(tmp_path, monkeypatch):
    host = FakeHostProcess()
    service = make_service(tmp_path, host_process=host)
    project = str(tmp_path / "project.hip")
    checkpoint = tmp_path / "backup" / "project_bak1.hip"
    checkpoint.parent.mkdir()
    checkpoint.write_text("checkpoint", encoding="utf-8")
    service.checkpoints.write("cp-force", {
        "checkpoint_id": "cp-force",
        "checkpoint_path": str(checkpoint),
        "original_hip": project,
        "verified": True,
        "adapter": "houdini",
        "workspace": "Bridge",
    })

    replacement = SimpleNamespace(
        session_id="HOU-NEW",
        pid=222,
        project_file=str(checkpoint),
    )
    monkeypatch.setattr(
        service,
        "_wait_for_replacement_session",
        lambda **kwargs: replacement,
    )
    executor = DirectRecoveryExecutor()
    service.register_executor("houdini", executor)

    report = service.force_recover_host(
        host_id="houdini",
        workspace="Bridge",
        pid=111,
        project_file=project,
    )

    assert report["status"] == "recovered"
    assert report["privilege_level"] == "L3_CORE_OS"
    assert report["target_source"] == "explicit_process"
    assert report["checkpoint_id"] == "cp-force"
    assert ("terminate", 111) in host.calls
    launch = next(item for item in host.calls if item[0] == "launch")
    assert launch[2] == str(checkpoint)
    assert len(executor.calls) == 1
    assert executor.calls[0].operation == "rollback.execute"
    assert executor.calls[0].execution.checkpoint == "none"


def test_force_recovery_relaunches_verified_checkpoint_after_old_pid_exits(tmp_path, monkeypatch):
    host = FakeHostProcess(running=False)
    service = make_service(tmp_path, host_process=host)
    project = str(tmp_path / "project.hip")
    checkpoint = tmp_path / "backup" / "project_bak2.hip"
    checkpoint.parent.mkdir()
    checkpoint.write_text("checkpoint", encoding="utf-8")
    executable = tmp_path / "houdini.exe"
    executable.write_text("binary-stub", encoding="utf-8")
    service.checkpoints.write("cp-dead-pid", {
        "checkpoint_id": "cp-dead-pid",
        "checkpoint_path": str(checkpoint),
        "original_hip": project,
        "verified": True,
        "adapter": "houdini",
        "workspace": "Bridge",
    })
    service.recoveries.write("prior-recovery", {
        "recovery_id": "prior-recovery",
        "status": "recovered",
        "host_id": "houdini",
        "workspace": "Bridge",
        "project_file": project,
        "host_executable": str(executable),
    })

    replacement = SimpleNamespace(
        session_id="HOU-NEW",
        pid=222,
        project_file=str(checkpoint),
    )
    monkeypatch.setattr(
        service,
        "_wait_for_replacement_session",
        lambda **kwargs: replacement,
    )
    executor = DirectRecoveryExecutor()
    service.register_executor("houdini", executor)

    report = service.force_recover_host(
        host_id="houdini",
        workspace="Bridge",
        pid=111,
        project_file=project,
        checkpoint_id="cp-dead-pid",
    )

    assert report["status"] == "recovered"
    assert report["host_executable"] == str(executable)
    assert report["host_executable_source"] == "recovery_history"
    assert report["termination"]["already_stopped"] is True
    assert not any(item[0] == "terminate" for item in host.calls)
    launch = next(item for item in host.calls if item[0] == "launch")
    assert launch[1] == str(executable)
    assert launch[2] == str(checkpoint)


def test_force_recovery_refuses_to_kill_without_checkpoint_by_default(tmp_path):
    host = FakeHostProcess()
    service = make_service(tmp_path, host_process=host)
    report = service.force_recover_host(
        host_id="houdini",
        workspace="Bridge",
        pid=111,
        project_file=str(tmp_path / "project.hip"),
        allow_saved_project_fallback=False,
    )

    assert report["status"] == "skipped"
    assert report["reason"] == "RECOVERY_CHECKPOINT_UNAVAILABLE"
    assert host.calls == []


def test_bridge_admin_exposes_force_recover_as_l3_control_capability():
    caps = {cap.name: cap for cap in bridge_admin_descriptor().capabilities}
    cap = caps["bridge.host.force_recover"]
    assert cap.write is True
    assert cap.risk.value == "L3"
    assert cap.manages_checkpoint is True
