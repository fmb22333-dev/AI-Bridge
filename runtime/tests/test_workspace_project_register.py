import json

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor, descriptor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _command(command_id, operation, arguments=None, workspace="Bridge"):
    return CommandEnvelope(
        command_id=command_id,
        workspace=workspace,
        adapter="workspace",
        operation=operation,
        arguments=arguments or {},
    )


def test_workspace_project_register_is_advertised_and_persisted(tmp_path):
    root = tmp_path / "bridge"
    child = root / "Projects" / "NotionCraftingGallery"
    child.mkdir(parents=True)
    registry = WorkspaceRegistry()
    registry.register("Bridge", root)
    supervisor = WorkerSupervisor(max_log_lines=32)
    workspaces_file = tmp_path / "data" / "workspaces.json"
    executor = WorkspaceWorkerExecutor(
        workspaces=registry, supervisor=supervisor, workspaces_file=workspaces_file
    )

    assert "workspace.project.register" in {cap.name for cap in descriptor().capabilities}
    result = executor.execute(
        _command(
            "register-1",
            "workspace.project.register",
            {"workspace_id": "notion-crafting-gallery", "path": "Projects/NotionCraftingGallery"},
        )
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["created"] is True
    assert result.result["persisted"] is True
    assert registry.get("notion-crafting-gallery").root == child.resolve()
    persisted = json.loads(workspaces_file.read_text(encoding="utf-8"))
    assert persisted == [
        {"workspace_id": "Bridge", "root": str(root.resolve())},
        {"workspace_id": "notion-crafting-gallery", "root": str(child.resolve())},
    ]

    repeat = executor.execute(
        _command(
            "register-2",
            "workspace.project.register",
            {"workspace_id": "notion-crafting-gallery", "path": "Projects/NotionCraftingGallery"},
        )
    )
    assert repeat.status == ExecutionStatus.SUCCESS
    assert repeat.result["created"] is False
    supervisor.close()


def test_workspace_project_register_rejects_escape_reserved_and_conflict(tmp_path):
    root = tmp_path / "bridge"
    first = root / "First"
    second = root / "Second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    registry = WorkspaceRegistry()
    registry.register("Bridge", root)
    supervisor = WorkerSupervisor(max_log_lines=32)
    executor = WorkspaceWorkerExecutor(
        workspaces=registry,
        supervisor=supervisor,
        workspaces_file=tmp_path / "data" / "workspaces.json",
    )

    escaped = executor.execute(
        _command("register-escape", "workspace.project.register", {"workspace_id": "escape", "path": "../outside"})
    )
    assert escaped.status == ExecutionStatus.DENIED
    assert escaped.failure.code == "WORKSPACE_PATH_ESCAPE"

    reserved = executor.execute(
        _command("register-reserved", "workspace.project.register", {"workspace_id": "__bad__", "path": "First"})
    )
    assert reserved.status == ExecutionStatus.DENIED
    assert reserved.failure.code == "WORKSPACE_SYSTEM_ID_RESERVED"

    first_result = executor.execute(
        _command("register-first", "workspace.project.register", {"workspace_id": "same", "path": "First"})
    )
    assert first_result.status == ExecutionStatus.SUCCESS
    conflict = executor.execute(
        _command("register-conflict", "workspace.project.register", {"workspace_id": "same", "path": "Second"})
    )
    assert conflict.status == ExecutionStatus.CONFLICT
    assert conflict.failure.code == "WORKSPACE_ALREADY_REGISTERED"
    supervisor.close()
