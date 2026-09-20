from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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


class FakeHostProcess:
    def __init__(self):
        self.calls = []

    def executable_path(self, pid):
        self.calls.append(("executable_path", pid))
        return "C:/Program Files/Side Effects Software/Houdini/bin/houdinifx.exe"

    def terminate(self, pid, *, force_after_seconds=1.0):
        self.calls.append(("terminate", pid))
        return {"pid": pid, "terminated": True, "elapsed_ms": 1.0}

    def launch(self, executable, project_file):
        self.calls.append(("launch", executable, project_file))
        return {"pid": 222, "executable": executable, "project_file": project_file}


def _base_service(tmp_path, *, host_process=None, auto_recover=False, budget=20):
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


def test_recovery_starts_verified_checkpoint_before_restoring_logical_project_name(tmp_path, monkeypatch):
    host = FakeHostProcess()
    service = _base_service(tmp_path, host_process=host, auto_recover=True)

    checkpoint_file = tmp_path / "backup" / "project_bak1.hip"
    checkpoint_file.parent.mkdir()
    checkpoint_file.write_text("checkpoint", encoding="utf-8")
    original_hip = str(tmp_path / "project.hip")
    checkpoint_id = "cp-test"
    service.checkpoints.write(checkpoint_id, {
        "checkpoint_id": checkpoint_id,
        "checkpoint_path": str(checkpoint_file),
        "original_hip": original_hip,
        "verified": True,
    })

    old_session = SimpleNamespace(
        session_id="HOU-OLD",
        pid=111,
        project_file=original_hip,
    )
    replacement = SimpleNamespace(
        session_id="HOU-NEW",
        pid=222,
        project_file=str(checkpoint_file),
    )
    monkeypatch.setattr(
        service,
        "_wait_for_replacement_session",
        lambda **kwargs: replacement,
    )

    executed = []

    def fake_execute(command):
        executed.append(command)
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )

    monkeypatch.setattr(service, "execute", fake_execute)

    source = CommandEnvelope.model_validate({
        "command_id": "source-cook",
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-OLD",
        "project_file": original_hip,
        "operation": "cook.execute",
        "arguments": {"path": "/obj/test"},
        "execution": {
            "verify": True,
            "checkpoint": "auto",
            "dry_run": False,
            "budget_seconds": 5,
            "auto_recover": True,
        },
        "risk": "L1",
    })

    report = service._recover_budget_exceeded(
        source,
        checkpoint_id=checkpoint_id,
        old_session=old_session,
    )

    assert report["status"] == "recovered"
    assert ("terminate", 111) in host.calls
    launch = [item for item in host.calls if item[0] == "launch"][0]
    assert launch[2] == str(checkpoint_file)

    assert len(executed) == 1
    rollback = executed[0]
    assert rollback.operation == "rollback.execute"
    assert rollback.project_file == str(checkpoint_file)
    assert rollback.arguments["checkpoint_path"] == str(checkpoint_file)
    assert rollback.arguments["original_hip"] == original_hip
    assert rollback.execution.auto_recover is False

    assert report["audit"]["recovery_boundary"] == checkpoint_id
    assert report["audit"]["reverted_or_unconfirmed_commands"][0]["command_id"] == "source-cook"
    persisted = service.recoveries.read(report["recovery_id"])
    assert persisted["status"] == "recovered"


def test_recovery_never_terminates_host_without_verified_checkpoint_reference(tmp_path):
    host = FakeHostProcess()
    service = _base_service(tmp_path, host_process=host, auto_recover=True)
    old_session = SimpleNamespace(
        session_id="HOU-OLD",
        pid=111,
        project_file=str(tmp_path / "project.hip"),
    )
    source = CommandEnvelope.model_validate({
        "command_id": "source-no-checkpoint",
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-OLD",
        "project_file": old_session.project_file,
        "operation": "inspect.context",
        "arguments": {},
        "execution": {
            "verify": True,
            "checkpoint": "auto",
            "dry_run": False,
            "budget_seconds": 5,
            "auto_recover": True,
        },
        "risk": "L1",
    })

    report = service._recover_budget_exceeded(
        source,
        checkpoint_id=None,
        old_session=old_session,
    )

    assert report["status"] == "skipped"
    assert report["reason"] == "RECOVERY_CHECKPOINT_UNAVAILABLE"
    assert host.calls == []


class RecordingQueueExecutor(QueueAdapterExecutor):
    def __init__(self, checkpoint_path, original_hip):
        self.checkpoint_path = checkpoint_path
        self.original_hip = original_hip
        self.calls = []

    def execute(self, command, *, timeout=None):
        self.calls.append((command.operation, timeout))
        if command.operation == "checkpoint.create":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": self.checkpoint_path,
                    "original_hip": self.original_hip,
                },
            )
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        )


def test_long_running_read_gets_checkpoint_when_auto_recovery_is_enabled(tmp_path):
    original_hip = str(tmp_path / "project.hip")
    checkpoint_path = str(tmp_path / "backup.hip")
    Path(checkpoint_path).write_text("checkpoint", encoding="utf-8")

    service = _base_service(tmp_path, auto_recover=True, budget=7)
    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-LONG",
        "adapter": "houdini",
        "adapter_version": "0.5.14",
        "host_version": "21.0.440",
        "pid": 1234,
        "project_file": original_hip,
        "capabilities": [
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
        ],
    }))
    executor = RecordingQueueExecutor(checkpoint_path, original_hip)
    service.register_executor("houdini", executor)

    command = CommandEnvelope.model_validate({
        "command_id": "long-cook",
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-LONG",
        "project_file": original_hip,
        "operation": "cook.execute",
        "arguments": {"path": "/obj/test"},
        "execution": {"verify": True, "checkpoint": "auto", "dry_run": False},
        "risk": "L1",
    })

    result = service.execute(command)

    assert result.status == ExecutionStatus.SUCCESS
    assert executor.calls[0] == ("checkpoint.create", 7.0)
    assert executor.calls[1] == ("cook.execute", 7)
    assert result.result["_bridge"]["checkpoint_id"].startswith("cp_long-cook_")
    assert result.result["_bridge"]["execution_budget"]["auto_recover"] is True
