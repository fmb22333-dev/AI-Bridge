from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin


class FakePlugins:
    def __init__(self, bundled="9.9.9"):
        self.bundled = bundled
        self.install_calls = []

    def install(self, host_id, *, host_running=False, force_clean=False):
        self.install_calls.append((host_id, host_running, force_clean))
        return {
            "host_id": host_id,
            "changed": False,
            "bundled_version": self.bundled,
        }

    def status(self, *, live_sessions=None):
        sessions = []
        for item in live_sessions or []:
            if item.adapter == "houdini":
                sessions.append({
                    "session_id": item.session_id,
                    "pid": item.pid,
                    "project_file": item.project_file,
                    "adapter_version": item.adapter_version,
                    "host_version": item.host_version,
                })
        return {
            "hosts": [{
                "id": "houdini",
                "state": "ready",
                "bundled_version": self.bundled,
                "disk_up_to_date": True,
                "live_sessions": sessions,
            }]
        }


class FakeHostProcess:
    def __init__(self):
        self.calls = []

    def executable_path(self, pid):
        self.calls.append(("executable_path", pid))
        return "C:/Houdini/bin/houdinifx.exe"

    def close_gracefully(self, pid, *, wait_seconds=8.0, force_if_needed=True):
        self.calls.append(("close_gracefully", pid, wait_seconds, force_if_needed))
        return {
            "pid": pid,
            "close_requested": True,
            "force_used": False,
            "terminated": True,
        }

    def launch(self, executable, project_file):
        self.calls.append(("launch", executable, project_file))
        return {
            "pid": 222,
            "executable": executable,
            "project_file": project_file,
        }


class CheckpointExecutor:
    def __init__(self, checkpoint_path, original_hip, *, fail=False):
        self.checkpoint_path = checkpoint_path
        self.original_hip = original_hip
        self.fail = fail
        self.calls = []

    def execute(self, command):
        self.calls.append(command.operation)
        if command.operation != "checkpoint.create":
            raise AssertionError(command.operation)
        if self.fail:
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER,
                    code="CHECKPOINT_FAILED",
                ),
            )
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={
                "verified": True,
                "checkpoint_path": self.checkpoint_path,
                "original_hip": self.original_hip,
            },
        )


def _service(tmp_path, *, old_version="0.5.13", checkpoint_fail=False):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", workspace_root)

    host = FakeHostProcess()
    plugins = FakePlugins("9.9.9")
    service = BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
        execution_policy=ExecutionPolicyStore(tmp_path / "execution_policy.json"),
        host_process=host,
        plugin_manager=plugins,
    )

    original_hip = str(tmp_path / "project.hip")
    checkpoint_path = str(tmp_path / "backup" / "project_bak1.hip")
    Path(checkpoint_path).parent.mkdir()
    Path(checkpoint_path).write_text("checkpoint", encoding="utf-8")

    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-OLD",
        "adapter": "houdini",
        "adapter_version": old_version,
        "host_version": "21.0.440",
        "pid": 111,
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
                "name": "rollback.execute",
                "version": "1.0",
                "write": True,
                "risk": "L2",
                "rollback": False,
                "verification": True,
                "tested_host_versions": ["21.0"],
            },
        ],
    }))

    executor = CheckpointExecutor(
        checkpoint_path,
        original_hip,
        fail=checkpoint_fail,
    )
    service.register_executor("houdini", executor)
    return service, host, plugins, executor, original_hip, checkpoint_path


def test_restart_apply_checkpoints_then_closes_launches_checkpoint_and_verifies_version(tmp_path, monkeypatch):
    service, host, plugins, executor, original_hip, checkpoint_path = _service(tmp_path)

    replacement_project = checkpoint_path.replace("/", "\\")
    replacement = SimpleNamespace(
        session_id="HOU-NEW",
        pid=222,
        project_file=replacement_project,
        adapter="houdini",
        adapter_version="9.9.9",
        host_version="21.0.440",
    )
    monkeypatch.setattr(
        service,
        "_wait_for_replacement_session",
        lambda **kwargs: replacement,
    )

    rollback_commands = []
    monkeypatch.setattr(
        service,
        "execute",
        lambda command: (
            rollback_commands.append(command)
            or ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={"verified": True},
            )
        ),
    )

    report = service.restart_host_for_plugin_update(
        host_id="houdini",
        session_id="HOU-OLD",
        workspace="Bridge",
        close_wait_seconds=3,
        session_wait_seconds=20,
    )

    assert report["status"] == "applied"
    assert report["adapter_version_verified"] is True
    assert report["old_adapter_version"] == "0.5.13"
    assert report["new_adapter_version"] == "9.9.9"
    assert executor.calls == ["checkpoint.create"]
    assert plugins.install_calls == [
        ("houdini", True, False),
        ("houdini", False, True),
    ]

    close_index = next(i for i, item in enumerate(host.calls) if item[0] == "close_gracefully")
    launch_index = next(i for i, item in enumerate(host.calls) if item[0] == "launch")
    assert close_index < launch_index
    assert host.calls[launch_index][2] == checkpoint_path

    assert len(rollback_commands) == 1
    rollback = rollback_commands[0]
    assert rollback.operation == "rollback.execute"
    assert rollback.project_file == replacement_project
    assert rollback.arguments["checkpoint_path"] == checkpoint_path
    assert rollback.arguments["original_hip"] == original_hip
    assert rollback.execution.auto_recover is False

    persisted = service.recoveries.read(report["recovery_id"])
    assert persisted["kind"] == "plugin_restart"
    assert persisted["status"] == "applied"


def test_restart_apply_never_closes_host_when_checkpoint_fails(tmp_path):
    service, host, plugins, executor, _, _ = _service(
        tmp_path,
        checkpoint_fail=True,
    )

    report = service.restart_host_for_plugin_update(
        host_id="houdini",
        session_id="HOU-OLD",
        workspace="Bridge",
    )

    assert report["status"] == "failed"
    assert report["reason"] == "CHECKPOINT_FAILED"
    assert executor.calls == ["checkpoint.create"]
    assert plugins.install_calls == [("houdini", True, False)]
    assert not any(item[0] == "close_gracefully" for item in host.calls)
    assert not any(item[0] == "launch" for item in host.calls)


def test_restart_apply_is_noop_when_live_adapter_already_matches_bundle(tmp_path):
    service, host, plugins, executor, _, _ = _service(
        tmp_path,
        old_version="9.9.9",
    )

    report = service.restart_host_for_plugin_update(
        host_id="houdini",
        session_id="HOU-OLD",
        workspace="Bridge",
    )

    assert report["status"] == "already_current"
    assert report["adapter_version_verified"] is True
    assert executor.calls == []
    assert plugins.install_calls == [("houdini", True, False)]
    assert host.calls == []


def test_force_restart_runs_full_flow_even_when_live_adapter_matches_bundle(tmp_path, monkeypatch):
    service, host, plugins, executor, original_hip, checkpoint_path = _service(
        tmp_path,
        old_version="9.9.9",
    )

    replacement = SimpleNamespace(
        session_id="HOU-NEW",
        pid=222,
        project_file=checkpoint_path,
        adapter="houdini",
        adapter_version="9.9.9",
        host_version="21.0.440",
    )
    monkeypatch.setattr(service, "_wait_for_replacement_session", lambda **kwargs: replacement)
    monkeypatch.setattr(
        service,
        "execute",
        lambda command: ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"verified": True},
        ),
    )

    report = service.restart_host_for_plugin_update(
        host_id="houdini",
        session_id="HOU-OLD",
        workspace="Bridge",
        force_restart=True,
    )

    assert report["status"] == "applied"
    assert report["force_restart"] is True
    assert executor.calls == ["checkpoint.create"]
    assert plugins.install_calls == [
        ("houdini", True, False),
        ("houdini", False, True),
    ]
    assert any(item[0] == "close_gracefully" for item in host.calls)
    assert any(item[0] == "launch" for item in host.calls)
