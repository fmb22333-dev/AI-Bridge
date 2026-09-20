import base64
import hashlib
import io
import zipfile

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor, descriptor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _command(command_id, arguments):
    return CommandEnvelope(
        command_id=command_id,
        workspace="Project",
        adapter="workspace",
        operation="workspace.artifact.import",
        arguments=arguments,
    )


def _executor(tmp_path, chunks):
    root = tmp_path / "project"
    root.mkdir()
    registry = WorkspaceRegistry()
    registry.register("Project", root)
    supervisor = WorkerSupervisor(max_log_lines=32)

    def fetcher(url, max_bytes):
        payload = chunks[url]
        assert len(payload) <= max_bytes
        return payload

    executor = WorkspaceWorkerExecutor(
        workspaces=registry,
        supervisor=supervisor,
        artifact_fetcher=fetcher,
    )
    return root, supervisor, executor


def test_artifact_import_accepts_ordered_base64_source_urls(tmp_path):
    payload = _zip_bytes({"package.json": '{"name":"chunked"}', "src/app.ts": "ok\n"})
    encoded = base64.b64encode(payload)
    cut = len(encoded) // 2
    urls = ["https://example.invalid/part0.b64", "https://example.invalid/part1.b64"]
    root, supervisor, executor = _executor(tmp_path, {urls[0]: encoded[:cut], urls[1]: encoded[cut:]})

    result = executor.execute(_command(
        "chunked-ok",
        {
            "source_urls": urls,
            "content_encoding": "base64",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result["verified"] is True
    assert result.result["source_count"] == 2
    assert result.result["content_encoding"] == "base64"
    assert (root / "package.json").read_text(encoding="utf-8") == '{"name":"chunked"}'
    assert (root / "src" / "app.ts").read_text(encoding="utf-8") == "ok\n"
    supervisor.close()


def test_artifact_import_rejects_invalid_base64_before_workspace_mutation(tmp_path):
    url = "https://example.invalid/bad.b64"
    root, supervisor, executor = _executor(tmp_path, {url: b"not-valid-base64!!!"})
    sentinel = root / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = executor.execute(_command(
        "chunked-bad-base64",
        {
            "source_urls": [url],
            "content_encoding": "base64",
            "sha256": "0" * 64,
            "destination": ".",
            "format": "zip",
        },
    ))

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "ARTIFACT_BASE64_INVALID"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    supervisor.close()


def test_artifact_import_rejects_ambiguous_or_empty_chunk_sources(tmp_path):
    payload = _zip_bytes({"safe.txt": "safe"})
    encoded = base64.b64encode(payload)
    url = "https://example.invalid/part0.b64"
    root, supervisor, executor = _executor(tmp_path, {url: encoded})
    sha = hashlib.sha256(payload).hexdigest()

    ambiguous = executor.execute(_command(
        "chunked-ambiguous",
        {
            "source_url": url,
            "source_urls": [url],
            "content_encoding": "base64",
            "sha256": sha,
        },
    ))
    assert ambiguous.status == ExecutionStatus.DENIED
    assert ambiguous.failure.code == "ARTIFACT_SOURCE_AMBIGUOUS"

    empty = executor.execute(_command(
        "chunked-empty",
        {"source_urls": [], "content_encoding": "base64", "sha256": sha},
    ))
    assert empty.status == ExecutionStatus.DENIED
    assert empty.failure.code == "ARTIFACT_URL_REQUIRED"
    assert list(root.iterdir()) == []
    supervisor.close()
