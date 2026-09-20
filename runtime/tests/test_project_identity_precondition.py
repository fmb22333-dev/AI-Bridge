from __future__ import annotations

from pathlib import Path

from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


class SuccessExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, command):
        self.calls.append(command)
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
    service = BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
    )
    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-PATH",
        "adapter": "houdini",
        "adapter_version": "0.5.14",
        "host_version": "21.0.440",
        "pid": 1234,
        "project_file": r"E:\\AA\\backup\\project_bak1.hip",
        "capabilities": [{
            "name": "parm.write",
            "version": "1.0",
            "write": True,
            "risk": "L1",
            "rollback": False,
            "verification": True,
            "tested_host_versions": ["21.0"],
        }],
    }))
    executor = SuccessExecutor()
    service.register_executor("houdini", executor)
    return service, executor


def _command(command_id, project_file):
    return CommandEnvelope.model_validate({
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-PATH",
        "project_file": project_file,
        "operation": "parm.write",
        "arguments": {
            "path": "/obj/test",
            "parameter": "tx",
            "value": 1,
        },
        "execution": {
            "verify": True,
            "checkpoint": "none",
            "dry_run": False,
        },
        "risk": "L1",
    })


def test_write_precondition_accepts_equivalent_windows_path_separators(tmp_path):
    service, executor = _service(tmp_path)

    result = service.execute(
        _command("path-equivalent", "E:/AA/backup/project_bak1.hip")
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert len(executor.calls) == 1


def test_write_precondition_still_rejects_a_different_project(tmp_path):
    service, executor = _service(tmp_path)

    result = service.execute(
        _command("path-different", "E:/AA/backup/other_project.hip")
    )

    assert result.status == ExecutionStatus.CONFLICT
    assert result.failure is not None
    assert result.failure.code == "PROJECT_FILE_MISMATCH"
    assert executor.calls == []


def test_same_project_handles_drive_case_and_separator_aliases(tmp_path):
    service, _ = _service(tmp_path)

    assert service._same_project(
        r"E:\\AA\\UVAutoChart.hip",
        "e:/aa/UVAutoChart.hip",
    )
