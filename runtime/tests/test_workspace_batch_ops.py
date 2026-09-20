import hashlib
import json
import sys

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor, descriptor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _runtime(tmp_path):
    registry = WorkspaceRegistry()
    registry.register("Dev", tmp_path)
    supervisor = WorkerSupervisor(max_log_lines=32)
    return WorkspaceWorkerExecutor(workspaces=registry, supervisor=supervisor), supervisor


def _command(command_id, operation, arguments=None):
    return CommandEnvelope(command_id=command_id, workspace="Dev", adapter="workspace", operation=operation, arguments=arguments or {})


def _manifest(tmp_path):
    path = tmp_path / ".ai-bridge" / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "project": "batch-sample",
        "commands": {
            "pass": {"argv": [sys.executable, "-S", "-c", "print('ok')"], "timeout_seconds": 10},
            "fail": {"argv": [sys.executable, "-S", "-c", "import sys; sys.exit(7)"], "timeout_seconds": 10}
        },
        "services": {}
    }), encoding="utf-8")


def test_descriptor_exposes_batch_capabilities():
    names = {cap.name for cap in descriptor().capabilities}
    assert "workspace.files.read" in names
    assert "workspace.patchset.apply" in names


def test_files_read_batches_success_error_and_budget(tmp_path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bravo-charlie", encoding="utf-8")
    executor, supervisor = _runtime(tmp_path)
    result = executor.execute(_command("read-many", "workspace.files.read", {
        "paths": ["a.txt", "missing.txt", "b.txt"],
        "max_chars_per_file": 5,
        "max_total_chars": 8
    }))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["count"] == 3
    assert result.result["items"][0]["text"] == "alpha"
    assert result.result["items"][1]["error"]["code"] == "WORKSPACE_FILE_NOT_FOUND"
    assert result.result["items"][2]["truncated"] is True
    assert result.result["total_returned_chars"] <= 8
    supervisor.close()


def test_patchset_preflight_conflict_writes_nothing(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    executor, supervisor = _runtime(tmp_path)
    result = executor.execute(_command("patchset-conflict", "workspace.patchset.apply", {"changes": [
        {"op": "replace", "path": "first.txt", "expected_hash": hashlib.sha256(first.read_bytes()).hexdigest(), "text": "ONE"},
        {"op": "patch", "path": "second.txt", "expected_hash": "stale", "old_text": "two", "new_text": "TWO"}
    ]}))
    assert result.status == ExecutionStatus.CONFLICT
    assert first.read_text(encoding="utf-8") == "one"
    assert second.read_text(encoding="utf-8") == "two"
    supervisor.close()


def test_patchset_applies_create_patch_replace_delete_atomically(tmp_path):
    patch = tmp_path / "patch.txt"
    replace = tmp_path / "replace.txt"
    delete = tmp_path / "delete.txt"
    patch.write_text("hello old", encoding="utf-8")
    replace.write_text("before", encoding="utf-8")
    delete.write_text("gone", encoding="utf-8")
    executor, supervisor = _runtime(tmp_path)
    result = executor.execute(_command("patchset-ok", "workspace.patchset.apply", {"changes": [
        {"op": "create", "path": "nested/new.txt", "text": "new", "create_parents": True},
        {"op": "patch", "path": "patch.txt", "expected_hash": hashlib.sha256(patch.read_bytes()).hexdigest(), "old_text": "old", "new_text": "new"},
        {"op": "replace", "path": "replace.txt", "expected_hash": hashlib.sha256(replace.read_bytes()).hexdigest(), "text": "after"},
        {"op": "delete", "path": "delete.txt", "expected_hash": hashlib.sha256(delete.read_bytes()).hexdigest()}
    ]}))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["atomic"] is True
    assert len(result.result["changed_files"]) == 4
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "new"
    assert patch.read_text(encoding="utf-8") == "hello new"
    assert replace.read_text(encoding="utf-8") == "after"
    assert not delete.exists()
    supervisor.close()


def test_patchset_protects_control_files(tmp_path):
    _manifest(tmp_path)
    executor, supervisor = _runtime(tmp_path)
    result = executor.execute(_command("patchset-control", "workspace.patchset.apply", {"changes": [
        {"op": "replace", "path": ".ai-bridge/project.json", "expected_hash": "x", "text": "{}"}
    ]}))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "WORKSPACE_CONTROL_FILE_PROTECTED"
    supervisor.close()


def test_patchset_failed_post_command_rolls_back_files(tmp_path):
    _manifest(tmp_path)
    target = tmp_path / "main.txt"
    target.write_text("before", encoding="utf-8")
    executor, supervisor = _runtime(tmp_path)
    result = executor.execute(_command("patchset-post-fail", "workspace.patchset.apply", {
        "changes": [{"op": "replace", "path": "main.txt", "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(), "text": "after"}],
        "post_commands": ["pass", "fail"]
    }))
    assert result.status == ExecutionStatus.FAILED
    assert result.failure.code == "PATCHSET_POST_COMMAND_FAILED"
    assert result.result["rolled_back"] is True
    assert target.read_text(encoding="utf-8") == "before"
    assert [item["command"] for item in result.result["post_commands"]] == ["pass", "fail"]
    supervisor.close()
