from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ai_bridge.adapters import workspace_worker as _workspace_worker
from ai_bridge.core.worker_manifest import MANIFEST_RELATIVE_PATH, ProjectManifest, load_project_manifest
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


_BaseWorkspaceWorkerExecutor = _workspace_worker.WorkspaceWorkerExecutor
_base_descriptor = _workspace_worker.descriptor


def descriptor():
    base = _base_descriptor()
    if not any(item.name == 'workspace.project.configure' for item in base.capabilities):
        capability = CapabilityDescriptor(
            name='workspace.project.configure',
            version='1.0',
            write=True,
            host_mutation=False,
            risk=RiskLevel.L2,
        )
        insert_at = next(
            (index + 1 for index, item in enumerate(base.capabilities) if item.name == 'workspace.project.register'),
            len(base.capabilities),
        )
        base.capabilities.insert(insert_at, capability)
    return base


class WorkspaceWorkerExecutor(_BaseWorkspaceWorkerExecutor):
    def _project_configure(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        payload = command.arguments.get('manifest')
        if not isinstance(payload, dict):
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                'WORKSPACE_MANIFEST_REQUIRED',
            )
        try:
            manifest = ProjectManifest.model_validate(payload)
        except ValidationError as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                'WORKSPACE_MANIFEST_INVALID',
                str(exc),
            )

        target = Path(workspace.root) / MANIFEST_RELATIVE_PATH
        serialized = json.dumps(
            manifest.model_dump(mode='json'),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + '\n'
        raw = serialized.encode('utf-8')
        expected_hash = str(command.arguments.get('expected_hash') or '').strip()

        with self._patch_lock:
            if target.exists() and not target.is_file():
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    'WORKSPACE_MANIFEST_PATH_CONFLICT',
                    str(target),
                )

            before = target.read_bytes() if target.is_file() else None
            before_hash = self._hash(before) if before is not None else None
            if before is not None and not expected_hash:
                return self._failure(
                    command.command_id,
                    ExecutionStatus.DENIED,
                    'EXPECTED_HASH_REQUIRED',
                    result={'current_hash': before_hash},
                )
            if before is None and expected_hash:
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    'EXPECTED_HASH_MISMATCH',
                    result={'current_hash': None},
                )
            if before is not None and before_hash != expected_hash:
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    'EXPECTED_HASH_MISMATCH',
                    result={'current_hash': before_hash},
                )

            created = before is None
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._patchset_atomic_write(target, raw)
                verified_manifest = load_project_manifest(workspace.root)
                if verified_manifest.model_dump(mode='json') != manifest.model_dump(mode='json'):
                    raise RuntimeError('manifest readback mismatch')
            except Exception as exc:
                try:
                    if before is None:
                        target.unlink(missing_ok=True)
                        try:
                            target.parent.rmdir()
                        except OSError:
                            pass
                    else:
                        self._patchset_atomic_write(target, before)
                except Exception as rollback_exc:
                    return self._failure(
                        command.command_id,
                        ExecutionStatus.FAILED,
                        'WORKSPACE_MANIFEST_ROLLBACK_FAILED',
                        f'{type(exc).__name__}: {exc}; rollback: {type(rollback_exc).__name__}: {rollback_exc}',
                    )
                return self._failure(
                    command.command_id,
                    ExecutionStatus.FAILED,
                    'WORKSPACE_MANIFEST_VERIFY_FAILED',
                    f'{type(exc).__name__}: {exc}',
                )

            after = target.read_bytes()
            return self._success(
                command.command_id,
                {
                    'path': str(MANIFEST_RELATIVE_PATH).replace('\\', '/'),
                    'project': manifest.project,
                    'commands': sorted(manifest.commands),
                    'services': sorted(manifest.services),
                    'created': created,
                    'before_hash': before_hash,
                    'sha256': self._hash(after),
                    'verified': after == raw,
                },
            )

    def execute(self, command: CommandEnvelope) -> ExecutionResult:
        if command.operation != 'workspace.project.configure':
            return super().execute(command)
        try:
            return self._project_configure(command)
        except KeyError as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                'WORKSPACE_NOT_FOUND',
                str(exc),
            )
        except Exception as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                'WORKSPACE_EXECUTION_ERROR',
                f'{type(exc).__name__}: {exc}',
            )


def install_workspace_project_configure() -> None:
    if getattr(_workspace_worker, '_structured_project_configure_installed', False):
        return
    _workspace_worker.WorkspaceWorkerExecutor = WorkspaceWorkerExecutor
    _workspace_worker.descriptor = descriptor
    _workspace_worker._structured_project_configure_installed = True
