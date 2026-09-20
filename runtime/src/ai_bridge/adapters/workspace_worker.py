from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ipaddress
import os
import shutil
import socket
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import RLock
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import ValidationError

from ai_bridge.adapters.registry import AdapterDescriptor
from ai_bridge.config import save_workspaces
from ai_bridge.core.worker_manifest import CommandSpec, ProjectManifest, ServiceSpec, load_project_manifest
from ai_bridge.core.worker_supervisor import (
    WorkerAlreadyRunning,
    WorkerNotRunning,
    WorkerReloadManual,
    WorkerSupervisor,
)
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin


ADAPTER_NAME = "workspace"
ADAPTER_VERSION = "1.0"
CONTROL_DIR_NAME = ".ai-bridge"
MAX_CREATE_BYTES = 2_000_000
MAX_BATCH_READ_FILES = 64
MAX_BATCH_READ_TOTAL_CHARS = 500_000
MAX_PATCHSET_CHANGES = 64
MAX_PATCHSET_TOTAL_BYTES = 4_000_000
MAX_ARTIFACT_DOWNLOAD_BYTES = 50_000_000
MAX_ARTIFACT_ENCODED_BYTES = ((MAX_ARTIFACT_DOWNLOAD_BYTES + 2) // 3) * 4 + 4096
MAX_ARTIFACT_SOURCE_URLS = 64
MAX_ARTIFACT_EXTRACTED_BYTES = 500_000_000
MAX_ARTIFACT_ENTRIES = 10_000


class ArtifactImportError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def descriptor() -> AdapterDescriptor:
    read = dict(write=False, host_mutation=False, risk=RiskLevel.L1)
    write = dict(write=True, host_mutation=False, risk=RiskLevel.L2)
    caps = [
        CapabilityDescriptor(name="workspace.project.inspect", version="1.0", **read),
        CapabilityDescriptor(name="workspace.project.register", version="1.0", **write),
        CapabilityDescriptor(name="workspace.artifact.import", version="1.0", **write),
        CapabilityDescriptor(name="workspace.directory.list", version="1.0", **read),
        CapabilityDescriptor(name="workspace.file.read", version="1.0", **read),
        CapabilityDescriptor(name="workspace.files.read", version="1.0", **read),
        CapabilityDescriptor(name="workspace.service.status", version="1.0", **read),
        CapabilityDescriptor(name="workspace.service.logs", version="1.0", **read),
        CapabilityDescriptor(name="workspace.health", version="1.0", **read),
        CapabilityDescriptor(name="workspace.file.create", version="1.0", **write),
        CapabilityDescriptor(name="workspace.file.patch", version="1.0", **write),
        CapabilityDescriptor(name="workspace.patchset.apply", version="1.0", **write),
        CapabilityDescriptor(name="workspace.command.run", version="1.0", **write),
        CapabilityDescriptor(name="workspace.service.start", version="1.0", **write),
        CapabilityDescriptor(name="workspace.service.stop", version="1.0", **write),
        CapabilityDescriptor(name="workspace.service.restart", version="1.0", **write),
        CapabilityDescriptor(name="workspace.service.reload", version="1.0", **write),
    ]
    return AdapterDescriptor(
        adapter=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        endpoint="local://workspace-worker",
        capabilities=caps,
    )


class WorkspaceWorkerExecutor:
    def __init__(
        self,
        *,
        workspaces: WorkspaceRegistry,
        supervisor: WorkerSupervisor,
        workspaces_file: Path | None = None,
        artifact_fetcher: Callable[[str, int], bytes] | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.supervisor = supervisor
        self.workspaces_file = Path(workspaces_file) if workspaces_file is not None else None
        self.artifact_fetcher = artifact_fetcher or self._download_artifact
        self._patch_lock = RLock()
        self._workspace_lock = RLock()
        self._artifact_lock = RLock()

    @staticmethod
    def _success(command_id: str, result: dict) -> ExecutionResult:
        return ExecutionResult(command_id=command_id, status=ExecutionStatus.SUCCESS, result=result)

    @staticmethod
    def _failure(
        command_id: str,
        status: ExecutionStatus,
        code: str,
        message: str | None = None,
        *,
        origin: FailureOrigin = FailureOrigin.WORKSPACE,
        result: dict | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            command_id=command_id,
            status=status,
            result=result or {},
            failure=FailureInfo(origin=origin, code=code, message=message),
        )

    def _workspace(self, command: CommandEnvelope):
        return self.workspaces.get(command.workspace)

    def _manifest(self, command: CommandEnvelope) -> ProjectManifest:
        workspace = self._workspace(command)
        return load_project_manifest(workspace.root)

    @staticmethod
    def _resolve_path(root: Path, relative: str) -> Path:
        text = str(relative or "").strip()
        if not text or "\x00" in text:
            raise ValueError("WORKSPACE_PATH_ESCAPE")
        raw = Path(text)
        win = PureWindowsPath(text)
        if raw.is_absolute() or win.is_absolute() or win.drive:
            raise ValueError("WORKSPACE_PATH_ESCAPE")
        candidate = (Path(root).resolve() / raw).resolve(strict=False)
        try:
            candidate.relative_to(Path(root).resolve())
        except ValueError as exc:
            raise ValueError("WORKSPACE_PATH_ESCAPE") from exc
        return candidate

    @staticmethod
    def _is_control_path(root: Path, target: Path) -> bool:
        relative = target.resolve(strict=False).relative_to(Path(root).resolve())
        return bool(relative.parts) and relative.parts[0].casefold() == CONTROL_DIR_NAME.casefold()

    @classmethod
    def _resolve_cwd(cls, root: Path, relative: str) -> Path:
        candidate = cls._resolve_path(root, relative or ".")
        if not candidate.exists():
            raise FileNotFoundError(str(candidate))
        if not candidate.is_dir():
            raise NotADirectoryError(str(candidate))
        return candidate

    @staticmethod
    def _hash(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _project_inspect(self, command: CommandEnvelope) -> ExecutionResult:
        manifest = self._manifest(command)
        return self._success(
            command.command_id,
            {
                "schema_version": manifest.schema_version,
                "project": manifest.project,
                "commands": sorted(manifest.commands),
                "services": {
                    name: {"reload": spec.reload}
                    for name, spec in sorted(manifest.services.items())
                },
            },
        )

    def _project_register(self, command: CommandEnvelope) -> ExecutionResult:
        source_workspace = self._workspace(command)
        workspace_id = str(command.arguments.get("workspace_id") or "").strip()
        if not workspace_id:
            return self._failure(
                command.command_id, ExecutionStatus.DENIED, "WORKSPACE_ID_REQUIRED"
            )
        if workspace_id.startswith("__"):
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_SYSTEM_ID_RESERVED",
                workspace_id,
            )

        relative = str(command.arguments.get("path") or "").strip()
        if not relative:
            return self._failure(
                command.command_id, ExecutionStatus.DENIED, "WORKSPACE_PATH_REQUIRED"
            )
        target = self._resolve_path(source_workspace.root, relative)
        if not target.exists() or not target.is_dir():
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "WORKSPACE_ROOT_NOT_FOUND",
                str(target),
            )
        target = target.resolve()

        with self._workspace_lock:
            try:
                existing = self.workspaces.get(workspace_id)
            except KeyError:
                existing = None
            if existing is not None:
                if existing.root == target:
                    return self._success(
                        command.command_id,
                        {
                            "workspace_id": workspace_id,
                            "root": str(target),
                            "created": False,
                            "persisted": self.workspaces_file is not None,
                        },
                    )
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    "WORKSPACE_ALREADY_REGISTERED",
                    workspace_id,
                    result={"existing_root": str(existing.root)},
                )

            workspace = self.workspaces.register(workspace_id, target)
            if self.workspaces_file is not None:
                try:
                    save_workspaces(
                        self.workspaces_file,
                        [
                            item
                            for item in self.workspaces.list()
                            if not item.workspace_id.startswith("__")
                        ],
                    )
                except Exception as exc:
                    self.workspaces.remove(workspace_id)
                    return self._failure(
                        command.command_id,
                        ExecutionStatus.FAILED,
                        "WORKSPACE_PERSIST_FAILED",
                        f"{type(exc).__name__}: {exc}",
                    )

            return self._success(
                command.command_id,
                {
                    "workspace_id": workspace.workspace_id,
                    "root": str(workspace.root),
                    "created": True,
                    "persisted": self.workspaces_file is not None,
                },
            )

    @staticmethod
    def _validate_artifact_url(url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ArtifactImportError("ARTIFACT_URL_UNSUPPORTED", "Artifact URL must be a credential-free HTTPS URL")

    @classmethod
    def _validate_public_https_url(cls, url: str) -> None:
        cls._validate_artifact_url(url)
        parsed = urlparse(url)
        try:
            port = parsed.port or 443
            infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        except (OSError, ValueError) as exc:
            raise ArtifactImportError("ARTIFACT_DOWNLOAD_FAILED", f"Unable to resolve artifact host: {exc}") from exc
        addresses = {str(info[4][0]).split("%", 1)[0] for info in infos if info and info[4]}
        if not addresses:
            raise ArtifactImportError("ARTIFACT_DOWNLOAD_FAILED", "Artifact host resolved to no addresses")
        for text in addresses:
            try:
                address = ipaddress.ip_address(text)
            except ValueError as exc:
                raise ArtifactImportError("ARTIFACT_URL_UNSUPPORTED", f"Invalid resolved address: {text}") from exc
            if not address.is_global:
                raise ArtifactImportError("ARTIFACT_URL_UNSUPPORTED", "Artifact URL must resolve only to public addresses")

    @classmethod
    def _download_artifact(cls, url: str, max_bytes: int) -> bytes:
        cls._validate_public_https_url(url)
        request = Request(url, headers={"User-Agent": "AI-Bridge-Artifact-Import/1.0"})
        try:
            with urlopen(request, timeout=60.0) as response:
                final_url = str(response.geturl() or url)
                cls._validate_public_https_url(final_url)
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        declared_size = 0
                    if declared_size > max_bytes:
                        raise ArtifactImportError("ARTIFACT_DOWNLOAD_TOO_LARGE", f"Artifact exceeds {max_bytes} bytes")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ArtifactImportError("ARTIFACT_DOWNLOAD_TOO_LARGE", f"Artifact exceeds {max_bytes} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ArtifactImportError:
            raise
        except Exception as exc:
            raise ArtifactImportError("ARTIFACT_DOWNLOAD_FAILED", f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _artifact_member_path(name: str) -> Path | None:
        text = str(name or "")
        if not text or "\x00" in text:
            raise ArtifactImportError("ARTIFACT_ZIP_UNSAFE_PATH", "ZIP entry has an invalid name")
        windows = PureWindowsPath(text)
        normalized = text.replace("\\", "/")
        posix = PurePosixPath(normalized)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or ".." in posix.parts
            or ".." in windows.parts
        ):
            raise ArtifactImportError("ARTIFACT_ZIP_UNSAFE_PATH", f"Unsafe ZIP entry: {name}")
        parts = [part for part in posix.parts if part not in ("", ".")]
        if not parts:
            return None
        return Path(*parts)

    @staticmethod
    def _remove_import_tree(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _artifact_import(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        source_url = str(command.arguments.get("source_url") or "").strip()
        source_urls_value = command.arguments.get("source_urls")
        if source_urls_value is None:
            source_urls: list[str] = []
        elif isinstance(source_urls_value, list):
            source_urls = [str(item or "").strip() for item in source_urls_value]
            if any(not item for item in source_urls):
                return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_URL_REQUIRED")
        else:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_URLS_INVALID")

        source_paths_value = command.arguments.get("source_paths")
        if source_paths_value is None:
            source_paths: list[str] = []
        elif isinstance(source_paths_value, list):
            source_paths = [str(item or "").strip() for item in source_paths_value]
            if any(not item for item in source_paths):
                return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_PATH_REQUIRED")
        else:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_PATHS_INVALID")

        source_modes = int(bool(source_url)) + int(bool(source_urls)) + int(bool(source_paths))
        if source_modes > 1:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_AMBIGUOUS")
        if source_modes == 0:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_URL_REQUIRED")
        multi_source_count = len(source_paths) if source_paths else len(source_urls)
        if multi_source_count > MAX_ARTIFACT_SOURCE_URLS:
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "ARTIFACT_TOO_MANY_SOURCES",
                result={"max_sources": MAX_ARTIFACT_SOURCE_URLS, "source_count": multi_source_count},
            )

        content_encoding = str(
            command.arguments.get("content_encoding")
            or ("base64" if (source_urls or source_paths) else "identity")
        ).strip().lower()
        if (source_urls or source_paths) and content_encoding != "base64":
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_ENCODING_UNSUPPORTED", content_encoding)
        if source_url and content_encoding not in {"identity", "raw"}:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_ENCODING_UNSUPPORTED", content_encoding)

        try:
            for url in ([source_url] if source_url else source_urls):
                self._validate_artifact_url(url)
        except ArtifactImportError as exc:
            return self._failure(command.command_id, ExecutionStatus.DENIED, exc.code, exc.message)

        expected_sha = str(command.arguments.get("sha256") or "").strip().lower()
        if not expected_sha:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SHA256_REQUIRED")
        if len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SHA256_INVALID")

        archive_format = str(command.arguments.get("format") or "zip").strip().lower()
        if archive_format != "zip":
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_FORMAT_UNSUPPORTED", archive_format)

        destination = str(command.arguments.get("destination") or ".").strip() or "."
        target = self._resolve_path(workspace.root, destination)
        if self._is_control_path(workspace.root, target):
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_CONTROL_FILE_PROTECTED",
                "Artifact destination cannot be inside .ai-bridge",
            )
        if target.exists() and not target.is_dir():
            return self._failure(command.command_id, ExecutionStatus.CONFLICT, "ARTIFACT_DESTINATION_NOT_DIRECTORY", str(target))

        resolved_source_paths: list[Path] = []
        if source_paths:
            for relative in source_paths:
                try:
                    source_path = self._resolve_path(workspace.root, relative)
                except ValueError as exc:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_PATH_INVALID", str(exc))
                if self._is_control_path(workspace.root, source_path):
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_CONTROL_PATH_PROTECTED", relative)
                if not source_path.is_file():
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_SOURCE_FILE_NOT_FOUND", relative)
                resolved_source_paths.append(source_path)

        source_mode = "local_paths" if source_paths else ("remote_urls" if source_urls else "remote_url")
        source_count = len(source_paths) if source_paths else (len(source_urls) if source_urls else 1)
        cleanup_source_paths = bool(command.arguments.get("cleanup_source_paths", False))
        try:
            if source_paths:
                encoded_parts: list[bytes] = []
                encoded_total = 0
                for source_path in resolved_source_paths:
                    try:
                        part = source_path.read_bytes()
                    except OSError as exc:
                        return self._failure(command.command_id, ExecutionStatus.FAILED, "ARTIFACT_SOURCE_READ_FAILED", f"{type(exc).__name__}: {exc}")
                    encoded_total += len(part)
                    if encoded_total > MAX_ARTIFACT_ENCODED_BYTES:
                        return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_DOWNLOAD_TOO_LARGE")
                    encoded_parts.append(part)
                encoded = b"".join(encoded_parts)
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_BASE64_INVALID", str(exc))
            elif source_urls:
                encoded_parts = []
                encoded_total = 0
                for url in source_urls:
                    remaining = MAX_ARTIFACT_ENCODED_BYTES - encoded_total
                    if remaining <= 0:
                        raise ArtifactImportError("ARTIFACT_DOWNLOAD_TOO_LARGE", f"Encoded artifact exceeds {MAX_ARTIFACT_ENCODED_BYTES} bytes")
                    part = self.artifact_fetcher(url, remaining)
                    if not isinstance(part, (bytes, bytearray)):
                        raise ArtifactImportError("ARTIFACT_DOWNLOAD_FAILED", "Fetcher returned non-bytes payload")
                    encoded_total += len(part)
                    if encoded_total > MAX_ARTIFACT_ENCODED_BYTES:
                        raise ArtifactImportError("ARTIFACT_DOWNLOAD_TOO_LARGE", f"Encoded artifact exceeds {MAX_ARTIFACT_ENCODED_BYTES} bytes")
                    encoded_parts.append(bytes(part))
                encoded = b"".join(encoded_parts)
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_BASE64_INVALID", str(exc))
            else:
                payload = self.artifact_fetcher(source_url, MAX_ARTIFACT_DOWNLOAD_BYTES)
                if not isinstance(payload, (bytes, bytearray)):
                    raise ArtifactImportError("ARTIFACT_DOWNLOAD_FAILED", "Fetcher returned non-bytes payload")
                raw = bytes(payload)
        except ArtifactImportError as exc:
            return self._failure(command.command_id, ExecutionStatus.FAILED, exc.code, exc.message)
        except Exception as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "ARTIFACT_DOWNLOAD_FAILED",
                f"{type(exc).__name__}: {exc}",
            )
        if len(raw) > MAX_ARTIFACT_DOWNLOAD_BYTES:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_DOWNLOAD_TOO_LARGE")
        actual_sha = self._hash(raw)
        if actual_sha != expected_sha:
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "ARTIFACT_CHECKSUM_MISMATCH",
                result={"expected_sha256": expected_sha, "actual_sha256": actual_sha},
            )

        try:
            archive = zipfile.ZipFile(io.BytesIO(raw), "r")
        except (zipfile.BadZipFile, OSError) as exc:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_ZIP_INVALID", str(exc))

        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARTIFACT_ENTRIES:
                return self._failure(command.command_id, ExecutionStatus.DENIED, "ARTIFACT_ZIP_TOO_MANY_ENTRIES")
            members: list[tuple[zipfile.ZipInfo, Path]] = []
            declared_total = 0
            try:
                for info in infos:
                    relative = self._artifact_member_path(info.filename)
                    if relative is None:
                        continue
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ArtifactImportError("ARTIFACT_ZIP_SYMLINK", f"Symlink ZIP entry is not allowed: {info.filename}")
                    if info.flag_bits & 0x1:
                        raise ArtifactImportError("ARTIFACT_ZIP_INVALID", f"Encrypted ZIP entry is not allowed: {info.filename}")
                    live_candidate = (target / relative).resolve(strict=False)
                    try:
                        live_candidate.relative_to(target.resolve(strict=False))
                    except ValueError as exc:
                        raise ArtifactImportError("ARTIFACT_ZIP_UNSAFE_PATH", f"ZIP entry escapes destination: {info.filename}") from exc
                    if self._is_control_path(workspace.root, live_candidate):
                        raise ArtifactImportError("ARTIFACT_CONTROL_PATH_PROTECTED", f"ZIP entry targets .ai-bridge: {info.filename}")
                    if not info.is_dir():
                        declared_total += max(0, int(info.file_size))
                        if declared_total > MAX_ARTIFACT_EXTRACTED_BYTES:
                            raise ArtifactImportError("ARTIFACT_ZIP_TOO_LARGE", f"Extracted artifact exceeds {MAX_ARTIFACT_EXTRACTED_BYTES} bytes")
                    members.append((info, relative))
            except ArtifactImportError as exc:
                return self._failure(command.command_id, ExecutionStatus.DENIED, exc.code, exc.message)

            with self._artifact_lock:
                token = uuid.uuid4().hex[:12]
                stage = target.parent / f".{target.name}.ai_bridge_import_stage_{token}"
                backup = target.parent / f".{target.name}.ai_bridge_import_backup_{token}"
                target_existed = target.exists()
                swapped = False
                written_total = 0
                file_count = 0
                try:
                    if target_existed:
                        shutil.copytree(target, stage, symlinks=True)
                    else:
                        stage.mkdir(parents=True, exist_ok=False)
                    stage_root = stage.resolve()
                    for info, relative in members:
                        candidate = (stage / relative).resolve(strict=False)
                        try:
                            candidate.relative_to(stage_root)
                        except ValueError as exc:
                            raise ArtifactImportError("ARTIFACT_ZIP_UNSAFE_PATH", f"ZIP entry escapes staging root: {info.filename}") from exc
                        if info.is_dir():
                            candidate.mkdir(parents=True, exist_ok=True)
                            continue
                        candidate.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info, "r") as source, candidate.open("wb") as output:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                written_total += len(chunk)
                                if written_total > MAX_ARTIFACT_EXTRACTED_BYTES:
                                    raise ArtifactImportError("ARTIFACT_ZIP_TOO_LARGE", f"Extracted artifact exceeds {MAX_ARTIFACT_EXTRACTED_BYTES} bytes")
                                output.write(chunk)
                        file_count += 1

                    if target_existed:
                        target.replace(backup)
                        try:
                            stage.replace(target)
                            swapped = True
                        except Exception as swap_exc:
                            try:
                                backup.replace(target)
                            except Exception as rollback_exc:
                                raise ArtifactImportError(
                                    "ARTIFACT_ROLLBACK_FAILED",
                                    f"Swap failed ({swap_exc}); rollback failed ({rollback_exc})",
                                ) from rollback_exc
                            raise ArtifactImportError("ARTIFACT_SWAP_FAILED", f"{type(swap_exc).__name__}: {swap_exc}") from swap_exc
                    else:
                        stage.replace(target)
                        swapped = True
                except ArtifactImportError as exc:
                    if not swapped and stage.exists():
                        self._remove_import_tree(stage)
                    return self._failure(command.command_id, ExecutionStatus.FAILED, exc.code, exc.message)
                except Exception as exc:
                    if not swapped and stage.exists():
                        self._remove_import_tree(stage)
                    if backup.exists() and not target.exists():
                        try:
                            backup.replace(target)
                        except Exception as rollback_exc:
                            return self._failure(
                                command.command_id,
                                ExecutionStatus.FAILED,
                                "ARTIFACT_ROLLBACK_FAILED",
                                f"{type(rollback_exc).__name__}: {rollback_exc}",
                            )
                    return self._failure(
                        command.command_id,
                        ExecutionStatus.FAILED,
                        "ARTIFACT_IMPORT_FAILED",
                        f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    if stage.exists():
                        self._remove_import_tree(stage)

                backup_cleanup = True
                if backup.exists():
                    try:
                        self._remove_import_tree(backup)
                    except Exception:
                        backup_cleanup = False

                source_cleanup = True
                if source_paths and cleanup_source_paths:
                    for source_path in resolved_source_paths:
                        try:
                            source_path.unlink()
                        except FileNotFoundError:
                            continue
                        except Exception:
                            source_cleanup = False

                return self._success(
                    command.command_id,
                    {
                        "source_url": source_url or None,
                        "source_count": source_count,
                        "source_mode": source_mode,
                        "source_cleanup": source_cleanup if (source_paths and cleanup_source_paths) else None,
                        "content_encoding": content_encoding,
                        "destination": destination,
                        "sha256": actual_sha,
                        "bytes": len(raw),
                        "extracted_bytes": written_total,
                        "file_count": file_count,
                        "verified": True,
                        "atomic": True,
                        "mode": "overlay",
                        "backup_cleanup": backup_cleanup,
                    },
                )

    def _directory_list(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        relative = str(command.arguments.get("path") or ".")
        target = self._resolve_path(workspace.root, relative)
        if not target.is_dir():
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "WORKSPACE_DIRECTORY_NOT_FOUND",
                str(target),
            )
        max_entries = max(1, min(int(command.arguments.get("max_entries") or 200), 1000))
        entries = []
        truncated = False
        with os.scandir(target) as iterator:
            for entry in iterator:
                if len(entries) >= max_entries:
                    truncated = True
                    break
                if entry.is_symlink():
                    kind = "symlink"
                elif entry.is_dir(follow_symlinks=False):
                    kind = "directory"
                elif entry.is_file(follow_symlinks=False):
                    kind = "file"
                else:
                    kind = "other"
                entry_path = Path(entry.path).relative_to(workspace.root)
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry_path).replace("\\", "/"),
                        "kind": kind,
                    }
                )
        entries.sort(key=lambda item: item["name"].casefold())
        return self._success(
            command.command_id,
            {
                "path": str(target.relative_to(workspace.root)).replace("\\", "/") or ".",
                "entries": entries,
                "truncated": truncated,
                "max_entries": max_entries,
            },
        )

    def _file_read(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        target = self._resolve_path(workspace.root, str(command.arguments.get("path") or ""))
        if not target.is_file():
            return self._failure(command.command_id, ExecutionStatus.FAILED, "WORKSPACE_FILE_NOT_FOUND", str(target))
        raw = target.read_bytes()
        text = raw.decode("utf-8")
        max_chars = max(1000, min(int(command.arguments.get("max_chars") or 100000), 500000))
        truncated = len(text) > max_chars
        return self._success(
            command.command_id,
            {
                "path": str(target.relative_to(workspace.root)).replace("\\", "/"),
                "text": text[:max_chars] if truncated else text,
                "chars": len(text),
                "truncated": truncated,
                "sha256": self._hash(raw),
            },
        )

    def _files_read(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        paths = command.arguments.get("paths")
        if not isinstance(paths, list) or not paths:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "WORKSPACE_PATHS_REQUIRED")
        if len(paths) > MAX_BATCH_READ_FILES:
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_TOO_MANY_FILES",
                result={"max_files": MAX_BATCH_READ_FILES, "requested_files": len(paths)},
            )
        max_per_file = max(1, min(int(command.arguments.get("max_chars_per_file") or 100000), 500000))
        max_total = max(1, min(int(command.arguments.get("max_total_chars") or MAX_BATCH_READ_TOTAL_CHARS), MAX_BATCH_READ_TOTAL_CHARS))
        items = []
        returned_total = 0
        for value in paths:
            relative = str(value or "").strip()
            item = {"path": relative}
            try:
                target = self._resolve_path(workspace.root, relative)
            except ValueError:
                item["error"] = {"code": "WORKSPACE_PATH_ESCAPE"}
                items.append(item)
                continue
            if not target.is_file():
                item["error"] = {"code": "WORKSPACE_FILE_NOT_FOUND"}
                items.append(item)
                continue
            raw = target.read_bytes()
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                item["error"] = {"code": "WORKSPACE_FILE_NOT_UTF8", "message": str(exc)}
                item["sha256"] = self._hash(raw)
                items.append(item)
                continue
            remaining = max(0, max_total - returned_total)
            limit = min(max_per_file, remaining)
            returned = decoded[:limit] if limit else ""
            returned_total += len(returned)
            item.update({
                "path": str(target.relative_to(workspace.root)).replace("\\", "/"),
                "text": returned,
                "chars": len(decoded),
                "returned_chars": len(returned),
                "truncated": len(returned) < len(decoded),
                "sha256": self._hash(raw),
            })
            items.append(item)
        return self._success(command.command_id, {
            "count": len(items),
            "items": items,
            "total_returned_chars": returned_total,
            "max_total_chars": max_total,
        })

    @staticmethod
    def _patchset_compact_command_result(name: str, result: ExecutionResult) -> dict:
        payload = {
            "command": name,
            "status": result.status.value,
        }
        if isinstance(result.result, dict):
            for key in ("exit_code", "stdout", "stderr"):
                if key in result.result:
                    payload[key] = result.result[key]
        if result.failure is not None:
            payload["failure"] = {
                "code": result.failure.code,
                "message": result.failure.message,
            }
        return payload

    @staticmethod
    def _patchset_atomic_write(target: Path, raw: bytes) -> None:
        temp = target.with_name(target.name + f".ai_bridge_patchset_{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp")
        try:
            temp.write_bytes(raw)
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()

    def _patchset_restore(self, snapshots: dict[Path, bytes | None], created_dirs: list[Path]) -> bool:
        ok = True
        for target, before in snapshots.items():
            try:
                if before is None:
                    if target.exists() or target.is_symlink():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self._patchset_atomic_write(target, before)
            except Exception:
                ok = False
        for directory in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return ok

    def _patchset_apply(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        changes = command.arguments.get("changes")
        if not isinstance(changes, list) or not changes:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "PATCHSET_CHANGES_REQUIRED")
        if len(changes) > MAX_PATCHSET_CHANGES:
            return self._failure(
                command.command_id, ExecutionStatus.DENIED, "PATCHSET_TOO_MANY_CHANGES",
                result={"max_changes": MAX_PATCHSET_CHANGES, "requested_changes": len(changes)},
            )
        post_commands = command.arguments.get("post_commands") or []
        if not isinstance(post_commands, list) or any(not isinstance(name, str) or not name.strip() for name in post_commands):
            return self._failure(command.command_id, ExecutionStatus.DENIED, "PATCHSET_POST_COMMANDS_INVALID")

        with self._patch_lock:
            manifest = None
            if post_commands:
                try:
                    manifest = self._manifest(command)
                except Exception:
                    raise
                unknown = [name for name in post_commands if name not in manifest.commands]
                if unknown:
                    return self._failure(
                        command.command_id, ExecutionStatus.DENIED, "COMMAND_NOT_DECLARED",
                        str(unknown[0]), result={"undeclared_commands": unknown},
                    )

            planned = []
            seen = set()
            total_bytes = 0
            for index, change in enumerate(changes):
                if not isinstance(change, dict):
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "PATCHSET_CHANGE_INVALID", result={"index": index})
                op = str(change.get("op") or "").strip().lower()
                if op not in {"create", "patch", "replace", "delete"}:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "PATCHSET_OPERATION_UNSUPPORTED", op, result={"index": index})
                relative = str(change.get("path") or "").strip()
                try:
                    target = self._resolve_path(workspace.root, relative)
                except ValueError:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "WORKSPACE_PATH_ESCAPE", result={"index": index, "path": relative})
                normalized = str(target.relative_to(workspace.root)).replace("\\", "/")
                key = normalized.casefold()
                if key in seen:
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "PATCHSET_DUPLICATE_PATH", normalized)
                seen.add(key)
                if self._is_control_path(workspace.root, target):
                    return self._failure(command.command_id, ExecutionStatus.DENIED, "WORKSPACE_CONTROL_FILE_PROTECTED", normalized)

                before = None
                if target.exists():
                    if not target.is_file():
                        return self._failure(command.command_id, ExecutionStatus.CONFLICT, "WORKSPACE_FILE_NOT_FOUND", normalized)
                    before = target.read_bytes()
                if op == "create":
                    if before is not None:
                        return self._failure(command.command_id, ExecutionStatus.CONFLICT, "WORKSPACE_FILE_ALREADY_EXISTS", normalized)
                    value = change.get("text")
                    if not isinstance(value, str):
                        return self._failure(command.command_id, ExecutionStatus.DENIED, "TEXT_REQUIRED", result={"index": index})
                    after = value.encode("utf-8")
                    if not bool(change.get("create_parents", False)) and not target.parent.is_dir():
                        return self._failure(command.command_id, ExecutionStatus.FAILED, "WORKSPACE_PARENT_NOT_FOUND", str(target.parent))
                else:
                    if before is None:
                        return self._failure(command.command_id, ExecutionStatus.FAILED, "WORKSPACE_FILE_NOT_FOUND", normalized)
                    expected = str(change.get("expected_hash") or "").strip()
                    if not expected:
                        return self._failure(command.command_id, ExecutionStatus.DENIED, "EXPECTED_HASH_REQUIRED", result={"index": index})
                    current_hash = self._hash(before)
                    if current_hash != expected:
                        return self._failure(
                            command.command_id, ExecutionStatus.CONFLICT, "EXPECTED_HASH_MISMATCH",
                            result={"index": index, "path": normalized, "current_hash": current_hash},
                        )
                    if op == "delete":
                        after = None
                    elif op == "replace":
                        value = change.get("text")
                        if not isinstance(value, str):
                            return self._failure(command.command_id, ExecutionStatus.DENIED, "TEXT_REQUIRED", result={"index": index})
                        after = value.encode("utf-8")
                    else:
                        old_text = change.get("old_text")
                        new_text = change.get("new_text")
                        if not isinstance(old_text, str) or not old_text:
                            return self._failure(command.command_id, ExecutionStatus.DENIED, "OLD_TEXT_REQUIRED", result={"index": index})
                        if not isinstance(new_text, str):
                            return self._failure(command.command_id, ExecutionStatus.DENIED, "NEW_TEXT_REQUIRED", result={"index": index})
                        try:
                            source = before.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            return self._failure(command.command_id, ExecutionStatus.CONFLICT, "WORKSPACE_FILE_NOT_UTF8", str(exc), result={"index": index})
                        count = source.count(old_text)
                        if count == 0:
                            return self._failure(command.command_id, ExecutionStatus.CONFLICT, "OLD_TEXT_NOT_FOUND", result={"index": index})
                        if count > 1:
                            return self._failure(command.command_id, ExecutionStatus.CONFLICT, "OLD_TEXT_AMBIGUOUS", result={"index": index, "matches": count})
                        after = source.replace(old_text, new_text, 1).encode("utf-8")
                if after is not None:
                    if len(after) > MAX_CREATE_BYTES:
                        return self._failure(
                            command.command_id, ExecutionStatus.DENIED, "WORKSPACE_FILE_TOO_LARGE",
                            result={"index": index, "max_bytes": MAX_CREATE_BYTES, "requested_bytes": len(after)},
                        )
                    total_bytes += len(after)
                    if total_bytes > MAX_PATCHSET_TOTAL_BYTES:
                        return self._failure(
                            command.command_id, ExecutionStatus.DENIED, "PATCHSET_TOO_LARGE",
                            result={"max_bytes": MAX_PATCHSET_TOTAL_BYTES, "requested_bytes": total_bytes},
                        )
                planned.append({
                    "op": op, "path": normalized, "target": target, "before": before, "after": after,
                    "create_parents": bool(change.get("create_parents", False)),
                })

            snapshots = {item["target"]: item["before"] for item in planned}
            created_dirs = []
            changed_files = []
            post_results = []
            try:
                for item in planned:
                    target = item["target"]
                    before = item["before"]
                    after = item["after"]
                    if item["op"] == "create" and item["create_parents"]:
                        missing = []
                        parent = target.parent
                        root = workspace.root.resolve()
                        while parent != root and not parent.exists():
                            missing.append(parent)
                            parent = parent.parent
                        target.parent.mkdir(parents=True, exist_ok=True)
                        created_dirs.extend(missing)
                    if after is None:
                        target.unlink()
                    elif item["op"] == "create":
                        with target.open("xb") as stream:
                            stream.write(after)
                    else:
                        self._patchset_atomic_write(target, after)
                    changed_files.append({
                        "op": item["op"],
                        "path": item["path"],
                        "before_hash": self._hash(before) if before is not None else None,
                        "after_hash": self._hash(after) if after is not None else None,
                        "before_bytes": len(before) if before is not None else 0,
                        "after_bytes": len(after) if after is not None else 0,
                    })

                for name in post_commands:
                    post_command = command.model_copy(update={"operation": "workspace.command.run", "arguments": {"command": name}})
                    post_result = self._command_run(post_command)
                    post_results.append(self._patchset_compact_command_result(name, post_result))
                    if post_result.status != ExecutionStatus.SUCCESS:
                        restored = self._patchset_restore(snapshots, created_dirs)
                        return self._failure(
                            command.command_id, ExecutionStatus.FAILED, "PATCHSET_POST_COMMAND_FAILED", name,
                            result={
                                "atomic": True, "rolled_back": restored,
                                "changed_files": changed_files, "post_commands": post_results,
                            },
                        )
            except Exception as exc:
                restored = self._patchset_restore(snapshots, created_dirs)
                return self._failure(
                    command.command_id, ExecutionStatus.FAILED, "PATCHSET_APPLY_FAILED",
                    f"{type(exc).__name__}: {exc}",
                    result={"atomic": True, "rolled_back": restored, "changed_files": changed_files, "post_commands": post_results},
                )

            created = sum(1 for item in planned if item["op"] == "create")
            deleted = sum(1 for item in planned if item["op"] == "delete")
            modified = len(planned) - created - deleted
            return self._success(command.command_id, {
                "atomic": True,
                "rolled_back": False,
                "changed_files": changed_files,
                "diff_stat": {
                    "created": created, "modified": modified, "deleted": deleted,
                    "before_bytes": sum(item["before_bytes"] for item in changed_files),
                    "after_bytes": sum(item["after_bytes"] for item in changed_files),
                },
                "post_commands": post_results,
            })

    def _file_create(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        target = self._resolve_path(workspace.root, str(command.arguments.get("path") or ""))
        if self._is_control_path(workspace.root, target):
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_CONTROL_FILE_PROTECTED",
                "Workspace control files cannot be created through workspace.file.create",
            )
        text = command.arguments.get("text")
        if not isinstance(text, str):
            return self._failure(command.command_id, ExecutionStatus.DENIED, "TEXT_REQUIRED")
        raw = text.encode("utf-8")
        if len(raw) > MAX_CREATE_BYTES:
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_FILE_TOO_LARGE",
                result={"max_bytes": MAX_CREATE_BYTES, "requested_bytes": len(raw)},
            )
        if bool(command.arguments.get("create_parents", False)):
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.is_dir():
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "WORKSPACE_PARENT_NOT_FOUND",
                str(target.parent),
            )
        try:
            with target.open("x", encoding="utf-8", newline="") as stream:
                stream.write(text)
        except FileExistsError:
            return self._failure(
                command.command_id,
                ExecutionStatus.CONFLICT,
                "WORKSPACE_FILE_ALREADY_EXISTS",
                str(target),
            )
        after_raw = target.read_bytes()
        return self._success(
            command.command_id,
            {
                "path": str(target.relative_to(workspace.root)).replace("\\", "/"),
                "sha256": self._hash(after_raw),
                "bytes": len(after_raw),
                "verified": after_raw == raw,
            },
        )

    def _file_patch(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        target = self._resolve_path(workspace.root, str(command.arguments.get("path") or ""))
        if self._is_control_path(workspace.root, target):
            return self._failure(
                command.command_id,
                ExecutionStatus.DENIED,
                "WORKSPACE_CONTROL_FILE_PROTECTED",
                "Workspace control files cannot be modified through workspace.file.patch",
            )

        with self._patch_lock:
            if not target.is_file():
                return self._failure(command.command_id, ExecutionStatus.FAILED, "WORKSPACE_FILE_NOT_FOUND", str(target))

            expected = str(command.arguments.get("expected_hash") or "").strip()
            if not expected:
                return self._failure(command.command_id, ExecutionStatus.DENIED, "EXPECTED_HASH_REQUIRED")
            old_text = command.arguments.get("old_text")
            new_text = command.arguments.get("new_text")
            if not isinstance(old_text, str) or not old_text:
                return self._failure(command.command_id, ExecutionStatus.DENIED, "OLD_TEXT_REQUIRED")
            if not isinstance(new_text, str):
                return self._failure(command.command_id, ExecutionStatus.DENIED, "NEW_TEXT_REQUIRED")

            raw = target.read_bytes()
            current_hash = self._hash(raw)
            if current_hash != expected:
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    "EXPECTED_HASH_MISMATCH",
                    result={"current_hash": current_hash},
                )
            text = raw.decode("utf-8")
            count = text.count(old_text)
            if count == 0:
                return self._failure(command.command_id, ExecutionStatus.CONFLICT, "OLD_TEXT_NOT_FOUND")
            if count > 1:
                return self._failure(
                    command.command_id,
                    ExecutionStatus.CONFLICT,
                    "OLD_TEXT_AMBIGUOUS",
                    result={"matches": count},
                )

            updated = text.replace(old_text, new_text, 1)
            temp = target.with_name(
                target.name + f".ai_bridge_patch_{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
            )
            # Encode exact bytes instead of text-mode writing so CRLF/LF style is
            # preserved and Windows does not translate existing newlines twice.
            temp.write_bytes(updated.encode("utf-8"))
            temp.replace(target)
            after_raw = target.read_bytes()
            return self._success(
                command.command_id,
                {
                    "path": str(target.relative_to(workspace.root)).replace("\\", "/"),
                    "before_hash": current_hash,
                    "after_hash": self._hash(after_raw),
                    "verified": after_raw.decode("utf-8") == updated,
                },
            )

    def _command_run(self, command: CommandEnvelope) -> ExecutionResult:
        workspace = self._workspace(command)
        manifest = self._manifest(command)
        name = str(command.arguments.get("command") or "").strip()
        spec: CommandSpec | None = manifest.commands.get(name)
        if spec is None:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "COMMAND_NOT_DECLARED", name)

        cwd = self._resolve_cwd(workspace.root, spec.cwd)
        env = dict(os.environ)
        env.update(spec.env)
        timeout = float(command.arguments.get("timeout_seconds") or spec.timeout_seconds)
        timeout = max(1.0, min(timeout, float(spec.timeout_seconds), 7200.0))
        try:
            run = subprocess.run(
                list(spec.argv),
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "COMMAND_TIMEOUT",
                f"{name} exceeded {timeout:.3f}s",
                result={
                    "command": name,
                    "timeout_seconds": timeout,
                    "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
                },
            )

        payload = {
            "command": name,
            "exit_code": int(run.returncode),
            "stdout": (run.stdout or "")[-20000:],
            "stderr": (run.stderr or "")[-20000:],
        }
        if run.returncode != 0:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "COMMAND_EXIT_NONZERO",
                f"{name} exited with code {run.returncode}",
                result=payload,
            )
        return self._success(command.command_id, payload)

    def _service_spec(self, command: CommandEnvelope, name: str) -> tuple[Path, ServiceSpec] | ExecutionResult:
        workspace = self._workspace(command)
        manifest = self._manifest(command)
        spec = manifest.services.get(name)
        if spec is None:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "SERVICE_NOT_DECLARED", name)
        return workspace.root, spec

    def _service_operation(self, command: CommandEnvelope) -> ExecutionResult:
        name = str(command.arguments.get("service") or "").strip()
        if not name:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "SERVICE_NOT_DECLARED", name)

        op = command.operation
        if op == "workspace.service.status":
            return self._success(command.command_id, self.supervisor.status(command.workspace, name))
        if op == "workspace.service.logs":
            return self._success(
                command.command_id,
                self.supervisor.logs(
                    command.workspace,
                    name,
                    tail=int(command.arguments.get("tail") or 200),
                ),
            )
        if op == "workspace.service.stop":
            return self._success(command.command_id, self.supervisor.stop(command.workspace, name))

        resolved = self._service_spec(command, name)
        if isinstance(resolved, ExecutionResult):
            return resolved
        root, spec = resolved
        if op == "workspace.service.start":
            payload = self.supervisor.start(command.workspace, root, name, spec)
        elif op == "workspace.service.restart":
            payload = self.supervisor.restart(command.workspace, root, name, spec)
        elif op == "workspace.service.reload":
            payload = self.supervisor.reload(command.workspace, root, name, spec)
        else:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "CAPABILITY_NOT_SUPPORTED", op)
        return self._success(command.command_id, payload)

    def _health(self, command: CommandEnvelope) -> ExecutionResult:
        manifest = self._manifest(command)
        services = {
            name: self.supervisor.status(command.workspace, name)
            for name in sorted(manifest.services)
        }
        return self._success(
            command.command_id,
            {"project": manifest.project, "status": "ok", "services": services},
        )

    def execute(self, command: CommandEnvelope) -> ExecutionResult:
        try:
            if command.operation == "workspace.project.inspect":
                return self._project_inspect(command)
            if command.operation == "workspace.project.register":
                return self._project_register(command)
            if command.operation == "workspace.artifact.import":
                return self._artifact_import(command)
            if command.operation == "workspace.directory.list":
                return self._directory_list(command)
            if command.operation == "workspace.file.read":
                return self._file_read(command)
            if command.operation == "workspace.files.read":
                return self._files_read(command)
            if command.operation == "workspace.file.create":
                return self._file_create(command)
            if command.operation == "workspace.file.patch":
                return self._file_patch(command)
            if command.operation == "workspace.patchset.apply":
                return self._patchset_apply(command)
            if command.operation == "workspace.command.run":
                return self._command_run(command)
            if command.operation.startswith("workspace.service."):
                return self._service_operation(command)
            if command.operation == "workspace.health":
                return self._health(command)
            return self._failure(command.command_id, ExecutionStatus.DENIED, "CAPABILITY_NOT_SUPPORTED")
        except KeyError as exc:
            return self._failure(command.command_id, ExecutionStatus.DENIED, "WORKSPACE_NOT_FOUND", str(exc))
        except FileNotFoundError as exc:
            if str(exc).endswith(str(Path(".ai-bridge") / "project.json")):
                code = "WORKSPACE_MANIFEST_NOT_FOUND"
            else:
                code = "WORKSPACE_FILE_NOT_FOUND"
            return self._failure(command.command_id, ExecutionStatus.FAILED, code, str(exc))
        except (ValidationError, ValueError) as exc:
            if str(exc) == "WORKSPACE_PATH_ESCAPE":
                return self._failure(command.command_id, ExecutionStatus.DENIED, "WORKSPACE_PATH_ESCAPE")
            return self._failure(command.command_id, ExecutionStatus.FAILED, "WORKSPACE_MANIFEST_INVALID", str(exc))
        except WorkerAlreadyRunning as exc:
            return self._failure(command.command_id, ExecutionStatus.CONFLICT, exc.code, str(exc), origin=FailureOrigin.ADAPTER)
        except WorkerNotRunning as exc:
            return self._failure(command.command_id, ExecutionStatus.CONFLICT, exc.code, str(exc), origin=FailureOrigin.ADAPTER)
        except WorkerReloadManual as exc:
            return self._failure(command.command_id, ExecutionStatus.CONFLICT, exc.code, str(exc), origin=FailureOrigin.ADAPTER)
        except Exception as exc:
            return self._failure(
                command.command_id,
                ExecutionStatus.FAILED,
                "WORKSPACE_EXECUTION_ERROR",
                f"{type(exc).__name__}: {exc}",
                origin=FailureOrigin.ADAPTER,
            )
