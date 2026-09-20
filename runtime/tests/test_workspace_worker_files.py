import json

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _runtime(tmp_path):
    registry = WorkspaceRegistry()
    registry.register("Dev", tmp_path)
    supervisor = WorkerSupervisor(max_log_lines=32)
    executor = WorkspaceWorkerExecutor(workspaces=registry, supervisor=supervisor)
    return executor, supervisor


def _command(command_id, operation, arguments=None):
    return CommandEnvelope(
        command_id=command_id,
        workspace="Dev",
        adapter="workspace",
        operation=operation,
        arguments=arguments or {},
    )


def test_file_create_is_absent_only_and_can_create_parent_directories(tmp_path):
    executor, supervisor = _runtime(tmp_path)

    created = executor.execute(
        _command(
            "create-1",
            "workspace.file.create",
            {
                "path": "src/new_module.py",
                "text": "VALUE = 1\n",
                "create_parents": True,
            },
        )
    )
    assert created.status == ExecutionStatus.SUCCESS
    assert created.result["verified"] is True
    assert (tmp_path / "src" / "new_module.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    duplicate = executor.execute(
        _command(
            "create-2",
            "workspace.file.create",
            {"path": "src/new_module.py", "text": "VALUE = 2\n"},
        )
    )
    assert duplicate.status == ExecutionStatus.CONFLICT
    assert duplicate.failure.code == "WORKSPACE_FILE_ALREADY_EXISTS"
    assert (tmp_path / "src" / "new_module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    supervisor.close()


def test_file_create_cannot_write_workspace_control_plane(tmp_path):
    executor, supervisor = _runtime(tmp_path)

    result = executor.execute(
        _command(
            "create-control-1",
            "workspace.file.create",
            {"path": ".ai-bridge/project.json", "text": json.dumps({"project": "hijack"})},
        )
    )

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "WORKSPACE_CONTROL_FILE_PROTECTED"
    assert not (tmp_path / ".ai-bridge" / "project.json").exists()
    supervisor.close()


def test_directory_list_is_bounded_and_non_recursive(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "deep.py").write_text("deep", encoding="utf-8")
    executor, supervisor = _runtime(tmp_path)

    result = executor.execute(
        _command(
            "list-1",
            "workspace.directory.list",
            {"path": "src", "max_entries": 2},
        )
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["path"] == "src"
    assert len(result.result["entries"]) == 2
    assert result.result["truncated"] is True
    assert all("deep.py" not in item["path"] for item in result.result["entries"])
    supervisor.close()
