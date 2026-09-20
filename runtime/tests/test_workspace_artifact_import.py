import hashlib
import io
import zipfile
from pathlib import Path

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor, descriptor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _command(command_id, arguments):
    return CommandEnvelope(
        command_id=command_id,
        workspace="Project",
        adapter="workspace",
        operation="workspace.artifact.import",
        arguments=arguments,
    )


def _zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _executor(tmp_path, payload):
    root = tmp_path / "project"
    root.mkdir()
    registry = WorkspaceRegistry()
    registry.register("Project", root)
    supervisor = WorkerSupervisor(max_log_lines=32)
    executor = WorkspaceWorkerExecutor(
        workspaces=registry,
        supervisor=supervisor,
        artifact_fetcher=lambda url, max_bytes: payload,
    )
    return root, supervisor, executor


def test_artifact_import_is_advertised_and_atomically_overlays_workspace(tmp_path):
    payload = _zip_bytes({
        "package.json": '{"name":"demo"}',
        "src/app.ts": "export const ready = true;\n",
    })
    root, supervisor, executor = _executor(tmp_path, payload)
    control = root / ".ai-bridge" / "project.json"
    control.parent.mkdir()
    control.write_text('{"preserve":true}', encoding="utf-8")
    sentinel = root / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    assert "workspace.artifact.import" in {cap.name for cap in descriptor().capabilities}
    result = executor.execute(_command(
        "artifact-ok",
        {
            "source_url": "https://example.invalid/project.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["verified"] is True
    assert result.result["file_count"] == 2
    assert (root / "package.json").read_text(encoding="utf-8") == '{"name":"demo"}'
    assert (root / "src" / "app.ts").read_text(encoding="utf-8") == "export const ready = true;\n"
    assert control.read_text(encoding="utf-8") == '{"preserve":true}'
    assert sentinel.read_text(encoding="utf-8") == "keep"
    supervisor.close()


def test_artifact_import_checksum_mismatch_is_noop(tmp_path):
    payload = _zip_bytes({"new.txt": "new"})
    root, supervisor, executor = _executor(tmp_path, payload)
    sentinel = root / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = executor.execute(_command(
        "artifact-bad-hash",
        {
            "source_url": "https://example.invalid/project.zip",
            "sha256": "0" * 64,
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "ARTIFACT_CHECKSUM_MISMATCH"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (root / "new.txt").exists()
    supervisor.close()


def test_artifact_import_rejects_zip_slip_without_mutating_workspace(tmp_path):
    payload = _zip_bytes({"../escape.txt": "bad", "safe.txt": "safe"})
    root, supervisor, executor = _executor(tmp_path, payload)
    sentinel = root / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = executor.execute(_command(
        "artifact-zip-slip",
        {
            "source_url": "https://example.invalid/project.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "ARTIFACT_ZIP_UNSAFE_PATH"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "escape.txt").exists()
    assert not (root / "safe.txt").exists()
    supervisor.close()


def test_artifact_import_requires_https_and_sha256(tmp_path):
    payload = _zip_bytes({"safe.txt": "safe"})
    root, supervisor, executor = _executor(tmp_path, payload)

    insecure = executor.execute(_command(
        "artifact-http",
        {"source_url": "http://example.invalid/project.zip", "sha256": hashlib.sha256(payload).hexdigest()},
    ))
    assert insecure.status == ExecutionStatus.DENIED
    assert insecure.failure.code == "ARTIFACT_URL_UNSUPPORTED"

    missing_hash = executor.execute(_command(
        "artifact-no-hash",
        {"source_url": "https://example.invalid/project.zip"},
    ))
    assert missing_hash.status == ExecutionStatus.DENIED
    assert missing_hash.failure.code == "ARTIFACT_SHA256_REQUIRED"
    assert list(root.iterdir()) == []
    supervisor.close()


def test_artifact_import_swap_failure_rolls_back_existing_workspace(tmp_path, monkeypatch):
    payload = _zip_bytes({"replacement.txt": "new"})
    root, supervisor, executor = _executor(tmp_path, payload)
    sentinel = root / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_replace = Path.replace

    def fail_stage_replace(self, target):
        if ".ai_bridge_import_stage_" in self.name:
            raise OSError("injected swap failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_stage_replace)
    result = executor.execute(_command(
        "artifact-swap-failure",
        {
            "source_url": "https://example.invalid/project.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.FAILED
    assert result.failure.code == "ARTIFACT_SWAP_FAILED"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (root / "replacement.txt").exists()
    assert not list(tmp_path.glob(".project.ai_bridge_import_backup_*"))
    supervisor.close()
