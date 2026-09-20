import hashlib
import json
import sys
import threading
import time

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _runtime(tmp_path):
    registry = WorkspaceRegistry()
    registry.register("Dev", tmp_path)
    supervisor = WorkerSupervisor(max_log_lines=64)
    executor = WorkspaceWorkerExecutor(workspaces=registry, supervisor=supervisor)
    return executor, supervisor


def _manifest(tmp_path):
    path = tmp_path / ".ai-bridge" / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": "sample",
                "commands": {
                    "probe": {
                        "argv": [sys.executable, "-S", "-c", "print('probe-ok')"],
                        "timeout_seconds": 10,
                    }
                },
                "services": {
                    "web": {
                        "argv": [
                            sys.executable,
                            "-S",
                            "-u",
                            "-c",
                            "import time; print('web-ready', flush=True); time.sleep(30)",
                        ],
                        "reload": "restart",
                        "stop_timeout_seconds": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _command(command_id, operation, arguments=None):
    return CommandEnvelope(
        command_id=command_id,
        workspace="Dev",
        adapter="workspace",
        operation=operation,
        arguments=arguments or {},
    )


def test_workspace_project_inspect_and_named_command(tmp_path):
    _manifest(tmp_path)
    executor, supervisor = _runtime(tmp_path)

    inspected = executor.execute(_command("inspect-1", "workspace.project.inspect"))
    assert inspected.status == ExecutionStatus.SUCCESS
    assert inspected.result["project"] == "sample"
    assert inspected.result["commands"] == ["probe"]

    ran = executor.execute(
        _command("probe-1", "workspace.command.run", {"command": "probe"})
    )
    assert ran.status == ExecutionStatus.SUCCESS
    assert ran.result["exit_code"] == 0
    assert "probe-ok" in ran.result["stdout"]

    denied = executor.execute(
        _command("probe-2", "workspace.command.run", {"command": "not-declared"})
    )
    assert denied.status == ExecutionStatus.DENIED
    assert denied.failure.code == "COMMAND_NOT_DECLARED"

    supervisor.close()


def test_workspace_file_patch_uses_hash_and_exact_old_text(tmp_path):
    _manifest(tmp_path)
    target = tmp_path / "main.py"
    target.write_bytes(b"value = 1\r\n")
    executor, supervisor = _runtime(tmp_path)

    read = executor.execute(
        _command("read-1", "workspace.file.read", {"path": "main.py"})
    )
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    assert read.status == ExecutionStatus.SUCCESS
    assert read.result["sha256"] == expected

    patched = executor.execute(
        _command(
            "patch-1",
            "workspace.file.patch",
            {
                "path": "main.py",
                "expected_hash": expected,
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
        )
    )
    assert patched.status == ExecutionStatus.SUCCESS
    assert target.read_bytes() == b"value = 2\r\n"

    stale = executor.execute(
        _command(
            "patch-2",
            "workspace.file.patch",
            {
                "path": "main.py",
                "expected_hash": expected,
                "old_text": "value = 2",
                "new_text": "value = 3",
            },
        )
    )
    assert stale.status == ExecutionStatus.CONFLICT
    assert stale.failure.code == "EXPECTED_HASH_MISMATCH"

    supervisor.close()


def test_file_patch_serializes_hash_check_and_write(tmp_path):
    _manifest(tmp_path)
    target = tmp_path / "main.py"
    target.write_bytes(b"value = 1\r\n")
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    executor, supervisor = _runtime(tmp_path)
    results = []

    command = _command(
        "patch-serialized-1",
        "workspace.file.patch",
        {
            "path": "main.py",
            "expected_hash": expected,
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
    )

    with executor._patch_lock:
        thread = threading.Thread(target=lambda: results.append(executor.execute(command)))
        thread.start()
        time.sleep(0.05)
        assert thread.is_alive()
        assert target.read_bytes() == b"value = 1\r\n"

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert results[0].status == ExecutionStatus.SUCCESS
    assert target.read_bytes() == b"value = 2\r\n"
    supervisor.close()


def test_workspace_control_files_cannot_be_patched(tmp_path):
    manifest_path = _manifest(tmp_path)
    executor, supervisor = _runtime(tmp_path)
    raw = manifest_path.read_bytes()

    result = executor.execute(
        _command(
            "manifest-patch-1",
            "workspace.file.patch",
            {
                "path": ".ai-bridge/project.json",
                "expected_hash": hashlib.sha256(raw).hexdigest(),
                "old_text": '"project": "sample"',
                "new_text": '"project": "hijacked"',
            },
        )
    )

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "WORKSPACE_CONTROL_FILE_PROTECTED"
    assert manifest_path.read_bytes() == raw
    supervisor.close()


def test_workspace_rejects_path_escape(tmp_path):
    _manifest(tmp_path)
    executor, supervisor = _runtime(tmp_path)

    result = executor.execute(
        _command("escape-1", "workspace.file.read", {"path": "../outside.txt"})
    )
    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "WORKSPACE_PATH_ESCAPE"

    supervisor.close()


def test_workspace_reports_missing_manifest_explicitly(tmp_path):
    executor, supervisor = _runtime(tmp_path)

    result = executor.execute(_command("manifest-1", "workspace.project.inspect"))

    assert result.status == ExecutionStatus.FAILED
    assert result.failure.code == "WORKSPACE_MANIFEST_NOT_FOUND"
    supervisor.close()


def test_running_service_can_be_stopped_if_manifest_is_removed(tmp_path):
    manifest_path = _manifest(tmp_path)
    executor, supervisor = _runtime(tmp_path)

    started = executor.execute(
        _command("service-start-1", "workspace.service.start", {"service": "web"})
    )
    assert started.status == ExecutionStatus.SUCCESS
    assert started.result["state"] == "running"

    manifest_path.unlink()

    status = executor.execute(
        _command("service-status-1", "workspace.service.status", {"service": "web"})
    )
    assert status.status == ExecutionStatus.SUCCESS
    assert status.result["state"] == "running"

    stopped = executor.execute(
        _command("service-stop-1", "workspace.service.stop", {"service": "web"})
    )
    assert stopped.status == ExecutionStatus.SUCCESS
    assert stopped.result["state"] == "stopped"
    supervisor.close()
