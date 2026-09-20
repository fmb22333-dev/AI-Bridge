from __future__ import annotations

import base64
import hashlib
import io
import zipfile

from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()


def _executor(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    registry = WorkspaceRegistry()
    registry.register('workspace', root)
    executor = WorkspaceWorkerExecutor(workspaces=registry, supervisor=object())
    return root, executor


def _command(command_id: str, arguments: dict) -> CommandEnvelope:
    return CommandEnvelope.model_validate({
        'protocol': 'bridge/1',
        'command_id': command_id,
        'workspace': 'workspace',
        'adapter': 'workspace',
        'operation': 'workspace.artifact.import',
        'arguments': arguments,
        'execution': {'verify': True, 'checkpoint': 'none', 'dry_run': False, 'budget_seconds': 30},
        'risk': 'L2',
    })


def test_artifact_import_accepts_local_base64_source_paths_and_cleans_them(tmp_path):
    root, executor = _executor(tmp_path)
    payload = _zip_bytes({'package.json': '{\"name\":\"local-parts\"}', 'src/app.ts': 'ok\n'})
    encoded = base64.b64encode(payload)
    cut = len(encoded) // 2
    inbox = root / 'artifact_inbox'
    inbox.mkdir()
    (inbox / 'part00.b64').write_bytes(encoded[:cut])
    (inbox / 'part01.b64').write_bytes(encoded[cut:])

    result = executor.execute(_command('local-parts-ok', {
        'source_paths': ['artifact_inbox/part00.b64', 'artifact_inbox/part01.b64'],
        'content_encoding': 'base64',
        'sha256': hashlib.sha256(payload).hexdigest(),
        'destination': '.',
        'format': 'zip',
        'cleanup_source_paths': True,
    }))

    assert result.status == ExecutionStatus.SUCCESS
    assert (root / 'package.json').read_text(encoding='utf-8') == '{\"name\":\"local-parts\"}'
    assert (root / 'src' / 'app.ts').read_text(encoding='utf-8') == 'ok\n'
    assert not (inbox / 'part00.b64').exists()
    assert not (inbox / 'part01.b64').exists()
    assert result.result['source_count'] == 2
    assert result.result['source_mode'] == 'local_paths'


def test_artifact_import_rejects_mixed_remote_and_local_sources(tmp_path):
    root, executor = _executor(tmp_path)
    inbox = root / 'artifact_inbox'
    inbox.mkdir()
    (inbox / 'part00.b64').write_text('AAAA', encoding='ascii')
    result = executor.execute(_command('local-parts-ambiguous', {
        'source_url': 'https://example.com/artifact.zip',
        'source_paths': ['artifact_inbox/part00.b64'],
        'sha256': '0' * 64,
    }))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == 'ARTIFACT_SOURCE_AMBIGUOUS'


def test_artifact_import_rejects_missing_local_part(tmp_path):
    _root, executor = _executor(tmp_path)
    result = executor.execute(_command('local-parts-missing', {
        'source_paths': ['artifact_inbox/missing.b64'],
        'content_encoding': 'base64',
        'sha256': '0' * 64,
    }))
    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == 'ARTIFACT_SOURCE_FILE_NOT_FOUND'
