from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from ai_bridge.adapters.registry import AdapterDescriptor
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.core.plugin_manager import HostPluginManager
from ai_bridge.deployment.knowledge_gate import require_source_knowledge_publishable
from ai_bridge.deployment.github_provision import PRODUCT_REPOSITORY, PRODUCT_REF
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin
from ai_bridge.security.secret_store import default_secret_store
from ai_bridge.transport.remote_config import load_remote_config


SYSTEM_WORKSPACE = "__bridge_system__"
ADAPTER_NAME = "bridge_admin"
ALLOWED_TOP_LEVEL = {
    "src",
    "tests",
    "houdini_adapter",
    "blender_adapter",
    "unreal_adapter",
    "pyproject.toml",
    "launch_bridge.py",
    "install_houdini_adapter.py",
}

BOOTSTRAP_ROOT_FILES = {
    "AI_Bridge.bat",
    "START_AI_BRIDGE.bat",
    "OPEN_AI_BRIDGE.bat",
    "STOP_AI_BRIDGE.bat",
    "README_FIRST.txt",
}
BOOTSTRAP_TEXT_SUFFIXES = {".py", ".bat", ".cmd", ".ps1", ".txt", ".md", ".toml", ".json"}
BOOTSTRAP_SKIP_DIRS = {".venv", "__pycache__", ".git", "cache", "logs", "temp", "tmp", "data"}
BOOTSTRAP_SENSITIVE_TOKENS = {"secret", "token", "credential", "connection", ".env"}
BOOTSTRAP_BACKUP_NAME_MARKERS = {"_before_", ".bak", ".backup"}




def descriptor() -> AdapterDescriptor:
    caps = [
        CapabilityDescriptor(name="bridge.update.status", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.project.resume", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.bootstrap.inventory", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.bootstrap.build_installer", version="1.0", write=True, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.update.begin", version="1.0", write=True, risk=RiskLevel.L1, rollback=True),
        CapabilityDescriptor(name="bridge.update.read_files", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.update.write_file", version="1.0", write=True, risk=RiskLevel.L1, rollback=True),
        CapabilityDescriptor(name="bridge.update.write_files", version="1.0", write=True, risk=RiskLevel.L1, rollback=True),
        CapabilityDescriptor(name="bridge.update.delete_file", version="1.0", write=True, risk=RiskLevel.L1, rollback=True),
        CapabilityDescriptor(name="bridge.update.test_subset", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.update.validate", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.update.publish", version="1.0", write=True, risk=RiskLevel.L1, rollback=True),
        CapabilityDescriptor(name="bridge.supervisor.status", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.supervisor.upgrade", version="1.0", write=True, risk=RiskLevel.L2, rollback=True, manages_checkpoint=True),
        CapabilityDescriptor(name="bridge.adapter.stage_houdini", version="1.0", write=True, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.plugin.status", version="1.0", write=False, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.plugin.install", version="1.0", write=True, risk=RiskLevel.L1),
        CapabilityDescriptor(name="bridge.plugin.install_all", version="1.0", write=True, risk=RiskLevel.L1),
        CapabilityDescriptor(
            name="bridge.plugin.restart_apply",
            version="1.0",
            write=True,
            risk=RiskLevel.L2,
            manages_checkpoint=True,
        ),
        CapabilityDescriptor(
            name="bridge.host.force_recover",
            version="1.0",
            write=True,
            risk=RiskLevel.L3,
            manages_checkpoint=True,
        ),
    ]
    return AdapterDescriptor(
        adapter=ADAPTER_NAME,
        adapter_version="1.0",
        endpoint="local://bridge-admin",
        capabilities=caps,
    )


class BridgeAdminExecutor:
    """Restricted self-development executor.

    It never edits Runtime/Current in place. All writes are constrained to Runtime/Staging.
    Publishing uploads a validated bundle; Supervisor owns activation and rollback.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        plugin_status_provider: Callable[[], dict] | None = None,
        plugin_install_handler: Callable[[str], dict] | None = None,
        plugin_install_all_handler: Callable[[], dict] | None = None,
        plugin_restart_handler: Callable[[dict], dict] | None = None,
        host_recover_handler: Callable[[dict], dict] | None = None,
        project_resume_handler: Callable[[dict, str | None, int], dict] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.plugin_status_provider = plugin_status_provider
        self.plugin_install_handler = plugin_install_handler
        self.plugin_install_all_handler = plugin_install_all_handler
        self.plugin_restart_handler = plugin_restart_handler
        self.host_recover_handler = host_recover_handler
        self.project_resume_handler = project_resume_handler
        root_env = os.environ.get("AI_BRIDGE_ROOT")
        self.root = Path(root_env).resolve() if root_env else Path.cwd().resolve().parent.parent
        self.runtime_root = self.root / "Runtime"
        self.current = self.runtime_root / "Current"
        self.staging = self.runtime_root / "Staging"
        self.status_file = self.data_dir / "supervisor_status.json"
        self.update_config_file = self.data_dir / "update_source.json"
        self.update_check_file = self.data_dir / "supervisor.update_check"
        self.maintenance_lock_file = self.data_dir / "maintenance_transaction.json"
        self.secret_store = default_secret_store(self.data_dir)
        self.plugins = HostPluginManager()

    @staticmethod
    def _success(command_id: str, payload: dict, *, rollback: bool = False) -> ExecutionResult:
        return ExecutionResult(
            command_id=command_id,
            status=ExecutionStatus.SUCCESS,
            result=payload,
            rollback_available=rollback,
        )

    @staticmethod
    def _failure(
        command_id: str,
        code: str,
        message: str,
        origin: FailureOrigin = FailureOrigin.BUILD,
        *,
        result: dict | None = None,
        rollback: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            command_id=command_id,
            status=ExecutionStatus.FAILED,
            result=result or {},
            failure=FailureInfo(origin=origin, code=code, message=message),
            rollback_available=rollback,
        )

    @staticmethod
    def _conflict(
        command_id: str,
        code: str,
        message: str,
        *,
        result: dict | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            command_id=command_id,
            status=ExecutionStatus.CONFLICT,
            result=result or {},
            failure=FailureInfo(
                origin=FailureOrigin.CORE,
                stage="precondition",
                code=code,
                message=message,
            ),
        )

    def _fetch_repo_text(
        self,
        repository: str,
        ref: str,
        path: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> dict:
        timeout_seconds = max(1.0, min(float(timeout_seconds), 15.0))
        token = self._token()
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(
                self._contents_url(repository, path),
                headers=self._github_headers(token),
                params={"ref": ref},
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"GitHub authority fetch failed: {ref}:{path} HTTP {response.status_code}"
            )
        payload = response.json()
        encoded = str(payload.get("content") or "").replace("\n", "")
        if not encoded:
            raise RuntimeError(f"GitHub authority content missing: {ref}:{path}")
        raw = base64.b64decode(encoded)
        return {
            "text": raw.decode("utf-8"),
            "sha": payload.get("sha"),
            "ref": ref,
            "path": path,
        }

    @staticmethod
    def _bounded_authority_content(text: str, max_chars: int) -> tuple[str, bool]:
        max_chars = max(4000, min(int(max_chars), 60000))
        if len(text) <= max_chars:
            return text, False
        head = int(max_chars * 0.6)
        tail = max_chars - head
        marker = "\n\n... [AI_BRIDGE_AUTHORITY_CONTENT_TRUNCATED] ...\n\n"
        return text[:head] + marker + text[-tail:], True

    @staticmethod
    def _resume_state_digest(payload: dict) -> str:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _project_resume(self, command_id: str, args: dict) -> ExecutionResult:
        if self.project_resume_handler is None:
            return self._failure(
                command_id,
                "PROJECT_RESUME_HANDLER_UNAVAILABLE",
                "Project resume local aggregator is unavailable",
                FailureOrigin.CORE,
            )

        bus = self._bus_source()
        repository = str(bus.get("repository") or "").strip()
        index_ref = str(bus.get("branch") or "main").strip() or "main"
        timeout_seconds = float(args.get("authority_timeout_seconds") or 5.0)
        try:
            index_doc = self._fetch_repo_text(
                repository,
                index_ref,
                "PROJECT_STATE_INDEX.json",
                timeout_seconds=timeout_seconds,
            )
            index = json.loads(index_doc["text"])
        except Exception as exc:
            return self._failure(
                command_id,
                "PROJECT_INDEX_UNAVAILABLE",
                f"{type(exc).__name__}: {exc}",
                FailureOrigin.CORE,
            )

        project_selector = str(args.get("project") or "").strip() or None
        history_limit = max(1, min(int(args.get("history_limit") or 100), 200))
        local = self.project_resume_handler(index, project_selector, history_limit)
        failure = local.get("failure") if isinstance(local, dict) else None
        if isinstance(failure, dict):
            return self._failure(
                command_id,
                str(failure.get("code") or "PROJECT_RESUME_FAILED"),
                str(failure.get("message") or "Project resume failed"),
                FailureOrigin.CORE,
                result={
                    "project_selector": project_selector,
                    "candidates": local.get("candidates") or [],
                },
            )

        project_info = local.get("project") if isinstance(local.get("project"), dict) else {}
        authority_pointer = (
            project_info.get("authority")
            if isinstance(project_info.get("authority"), dict)
            else {}
        )
        authority_ref = str(authority_pointer.get("ref") or "").strip()
        authority_path = str(authority_pointer.get("path") or "").strip()
        authority_available = False
        authority_digest = None
        authority_error = None
        authority_payload = {
            "ref": authority_ref or None,
            "path": authority_path or None,
            "available": False,
            "content": None,
            "content_chars": 0,
            "truncated": False,
            "digest": None,
            "github_sha": None,
        }
        if authority_ref and authority_path:
            try:
                authority_doc = self._fetch_repo_text(
                    repository,
                    authority_ref,
                    authority_path,
                    timeout_seconds=timeout_seconds,
                )
                full_text = authority_doc["text"]
                rendered, truncated = self._bounded_authority_content(
                    full_text,
                    int(args.get("max_authority_chars") or 4500),
                )
                authority_digest = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
                authority_available = True
                authority_payload.update({
                    "available": True,
                    "content": rendered,
                    "content_chars": len(full_text),
                    "truncated": truncated,
                    "digest": authority_digest,
                    "github_sha": authority_doc.get("sha"),
                })
            except Exception as exc:
                authority_error = f"{type(exc).__name__}: {exc}"
                authority_payload["error"] = authority_error
        else:
            authority_error = "Project authority pointer is missing"
            authority_payload["error"] = authority_error

        project_info["authority"] = authority_payload
        local["project"] = project_info
        live = local.get("live") if isinstance(local.get("live"), dict) else {}
        runtime_version = self._version(self.current)
        live["bridge_version"] = runtime_version
        local["live"] = live
        resume = local.get("resume") if isinstance(local.get("resume"), dict) else {}
        warnings = list(resume.get("warnings") or [])
        if not authority_available:
            warnings.append("PROJECT_AUTHORITY_UNAVAILABLE")
            resume["safe_to_continue"] = False
        resume["authority_available"] = authority_available
        resume["warnings"] = list(dict.fromkeys(warnings))

        last_terminal = (
            local.get("execution", {}).get("last_terminal")
            if isinstance(local.get("execution"), dict)
            else None
        )
        token_state = {
            "project": project_info.get("key"),
            "project_file": project_info.get("current_hip"),
            "observed_session": live.get("session_id"),
            "last_terminal_command": (
                last_terminal.get("command_id")
                if isinstance(last_terminal, dict)
                else None
            ),
            "authority_ref": (
                f"{authority_ref}:{authority_path}"
                if authority_ref and authority_path
                else None
            ),
            "authority_digest": authority_digest,
            "runtime_version": runtime_version,
        }
        state_digest = self._resume_state_digest(token_state)
        resume_token = {
            **token_state,
            "state_digest": state_digest,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        resume["resume_token"] = resume_token
        local["resume"] = resume

        expected = args.get("expected_resume_token")
        if expected is not None:
            if not isinstance(expected, dict) or str(expected.get("state_digest") or "") != state_digest:
                resume["safe_to_continue"] = False
                resume["warnings"] = list(dict.fromkeys(
                    list(resume.get("warnings") or []) + ["RESUME_STATE_STALE"]
                ))
                return self._conflict(
                    command_id,
                    "RESUME_STATE_STALE",
                    "Observed project state no longer matches expected_resume_token",
                    result=local,
                )

        return self._success(command_id, local)

    @classmethod
    def _handler_outcome(
        cls,
        command_id: str,
        payload: dict,
        *,
        success_statuses: set[str],
        failure_code: str,
        rollback: bool = False,
    ) -> ExecutionResult:
        payload = dict(payload or {})
        status = str(payload.get("status") or "").strip().lower()
        if status in {str(item).strip().lower() for item in success_statuses}:
            return cls._success(command_id, payload, rollback=rollback)
        reason = str(payload.get("reason") or payload.get("message") or status or "UNKNOWN_OUTCOME")
        return cls._failure(
            command_id,
            failure_code,
            reason,
            FailureOrigin.ADAPTER,
            result=payload,
            rollback=rollback,
        )

    def _bootstrap_inventory(self, *, include_text: bool = False, include_backups: bool = False, max_chars_per_file: int = 40000) -> dict:
        max_chars_per_file = max(1000, min(int(max_chars_per_file), 100000))
        candidates: list[Path] = []
        for name in sorted(BOOTSTRAP_ROOT_FILES):
            path = self.root / name
            if path.is_file():
                candidates.append(path)

        system_root = self.root / "_System"
        if system_root.is_dir():
            for path in sorted(system_root.rglob("*")):
                if not path.is_file():
                    continue
                relative_parts = path.relative_to(system_root).parts
                if any(part.lower() in BOOTSTRAP_SKIP_DIRS for part in relative_parts[:-1]):
                    continue
                lowered_name = path.name.lower()
                if any(token in lowered_name for token in BOOTSTRAP_SENSITIVE_TOKENS):
                    continue
                if not include_backups and any(marker in lowered_name for marker in BOOTSTRAP_BACKUP_NAME_MARKERS):
                    continue
                candidates.append(path)

        files = []
        text_chars = 0
        for path in candidates[:500]:
            raw = path.read_bytes()
            relative = path.relative_to(self.root).as_posix()
            item = {
                "path": relative,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "suffix": path.suffix.lower(),
            }
            if include_text and path.suffix.lower() in BOOTSTRAP_TEXT_SUFFIXES and len(raw) <= max_chars_per_file:
                try:
                    text_value = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text_value = None
                if text_value is not None:
                    item["text"] = text_value
                    text_chars += len(text_value)
            files.append(item)

        return {
            "root": str(self.root),
            "system_root": str(system_root),
            "file_count": len(files),
            "include_text": bool(include_text),
            "include_backups": bool(include_backups),
            "text_chars": text_chars,
            "files": files,
            "exclusions": {
                "directories": sorted(BOOTSTRAP_SKIP_DIRS),
                "sensitive_name_tokens": sorted(BOOTSTRAP_SENSITIVE_TOKENS),
            },
        }

    @staticmethod
    def _version(path: Path) -> str:
        file = path / "pyproject.toml"
        if not file.exists():
            return "unknown"
        for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"\'')
        return "unknown"

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        if not re.fullmatch(r"\d+(?:\.\d+){2,3}", value):
            raise ValueError("version must look like 0.1.4.1")
        return tuple(int(part) for part in value.split("."))

    @staticmethod
    def _safe_runtime_path(root: Path, relative: str) -> Path:
        relative = str(relative or "").replace("\\", "/").lstrip("/")
        if not relative or relative.startswith("../") or "/../" in f"/{relative}/":
            raise ValueError("invalid relative path")
        top = relative.split("/", 1)[0]
        if top not in ALLOWED_TOP_LEVEL:
            raise ValueError(f"path is outside allowed Runtime surface: {top}")
        resolved_root = Path(root).resolve()
        target = (resolved_root / relative).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("path escapes Runtime surface") from exc
        return target

    def _safe_staging_path(self, relative: str) -> Path:
        return self._safe_runtime_path(self.staging, relative)

    def _read_files(self, paths: list, *, source: str = "staging", max_chars_per_file: int = 40000) -> dict:
        source = str(source or "staging").strip().lower()
        if source not in {"current", "staging"}:
            raise ValueError("source must be current or staging")
        root = self.current if source == "current" else self.staging
        if not root.exists():
            raise RuntimeError(f"Runtime/{source.title()} does not exist")
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty list")
        if len(paths) > 32:
            raise ValueError("paths exceeds maximum batch size 32")
        max_chars_per_file = max(1000, min(int(max_chars_per_file), 100000))

        files = []
        total_chars = 0
        for entry in paths:
            relative = str(entry or "").replace("\\", "/").lstrip("/")
            target = self._safe_runtime_path(root, relative)
            if not target.is_file():
                raise FileNotFoundError(relative)
            text = target.read_text(encoding="utf-8", errors="replace")
            truncated = len(text) > max_chars_per_file
            if truncated:
                text = text[:max_chars_per_file]
            files.append({
                "path": relative,
                "text": text,
                "chars": len(text),
                "truncated": truncated,
            })
            total_chars += len(text)
        return {"source": source, "count": len(files), "total_chars": total_chars, "files": files}

    def _write_files(self, files: list) -> dict:
        if not self.staging.exists():
            raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
        if not isinstance(files, list) or not files:
            raise ValueError("files must be a non-empty list")
        if len(files) > 32:
            raise ValueError("files exceeds maximum batch size 32")

        prepared = []
        seen = set()
        total_chars = 0
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise ValueError(f"files[{index}] must be an object")
            relative = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            target = self._safe_staging_path(relative)
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError(f"files[{index}].text must be a UTF-8 string")
            key = str(target).lower()
            if key in seen:
                raise ValueError(f"duplicate batch path: {relative}")
            seen.add(key)
            total_chars += len(text)
            if total_chars > 1_000_000:
                raise ValueError("batch text exceeds 1,000,000 characters")
            prepared.append((relative, target, text))

        temps = []
        backups = {}
        replaced = []
        try:
            for index, (relative, target, text) in enumerate(prepared):
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + f".ai_bridge_batch_{os.getpid()}_{index}.tmp")
                temp.write_text(text, encoding="utf-8")
                temps.append(temp)
                backups[target] = target.read_bytes() if target.exists() else None

            for (_, target, _), temp in zip(prepared, temps):
                temp.replace(target)
                replaced.append(target)
        except Exception:
            for target in reversed(replaced):
                before = backups.get(target)
                try:
                    if before is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(before)
                except Exception:
                    pass
            raise
        finally:
            for temp in temps:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass

        return {
            "count": len(prepared),
            "total_chars": total_chars,
            "files": [{"path": relative, "chars": len(text)} for relative, _, text in prepared],
        }

    def _test_subset(self, paths: list, *, k: str | None = None, timeout_sec: int = 90) -> dict:
        if not self.staging.exists():
            raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty list")
        if len(paths) > 16:
            raise ValueError("paths exceeds maximum subset size 16")

        selected = []
        resolved = []
        tests_root = (self.staging / "tests").resolve()
        for entry in paths:
            relative = str(entry or "").replace("\\", "/").lstrip("/")
            if not relative.startswith("tests/"):
                raise ValueError("subset paths must be under tests/")
            target = (self.staging / relative).resolve()
            try:
                target.relative_to(tests_root)
            except ValueError as exc:
                raise ValueError("subset path escapes tests/") from exc
            if not target.exists():
                raise FileNotFoundError(relative)
            selected.append(relative)
            resolved.append(str(target))

        command = [sys.executable, "-m", "pytest", "-q", *resolved]
        if k:
            k = str(k)
            if len(k) > 200:
                raise ValueError("-k expression is too long")
            command.extend(["-k", k])
        timeout_sec = max(10, min(int(timeout_sec), 180))
        started = time.perf_counter()
        run = subprocess.run(
            command,
            cwd=str(self.staging),
            env={**os.environ, "PYTHONPATH": str(self.staging / "src")},
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        output = (run.stdout + "\n" + run.stderr).strip()
        if run.returncode != 0:
            raise RuntimeError("pytest subset failed: " + output[-5000:])
        return {
            "tests": "PASS",
            "selected_paths": selected,
            "k": k,
            "elapsed_ms": round(elapsed_ms, 3),
            "pytest_output": output[-2000:],
        }

    def _source_mirror_snapshot(self, version: str) -> dict[str, str]:
        if not self.staging.exists():
            raise RuntimeError("Staging is not initialized")
        snapshot: dict[str, str] = {}
        for path in sorted(self.staging.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.staging)
            if "__pycache__" in rel.parts or ".pytest_cache" in rel.parts:
                continue
            if path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            snapshot["runtime-src/" + rel.as_posix()] = text

        index = {
            "version": str(version),
            "file_count": len(snapshot),
            "source": "Runtime/Staging",
            "purpose": "Direct-browse source mirror for AI Bridge development; runtime_bundle.zip remains the activation artifact.",
        }
        snapshot["runtime-src/SOURCE_INDEX.json"] = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
        return snapshot


    def _maintenance_read(self) -> dict | None:
        import time as _time
        try:
            data = json.loads(self.maintenance_lock_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            try: self.maintenance_lock_file.unlink()
            except OSError: pass
            return None
        if not isinstance(data, dict):
            return None
        if float(data.get("expires_at") or 0.0) <= _time.time():
            try: self.maintenance_lock_file.unlink()
            except OSError: pass
            return None
        return data

    def _maintenance_write(self, data: dict) -> None:
        temp = self.maintenance_lock_file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.maintenance_lock_file)

    def _maintenance_acquire(self, transaction_id: str) -> dict:
        import os as _os, time as _time
        transaction_id=str(transaction_id or "").strip()
        if not transaction_id:
            raise RuntimeError("MAINTENANCE_TRANSACTION_REQUIRED: transaction_id is required")
        current=self._maintenance_read()
        if current is not None and current.get("transaction_id") != transaction_id:
            raise RuntimeError("MAINTENANCE_LOCKED: active transaction "+str(current.get("transaction_id") or "unknown"))
        now=_time.time()
        data={"transaction_id":transaction_id,"acquired_at":float((current or {}).get("acquired_at") or now),"expires_at":now+1800.0,"owner_pid":_os.getpid()}
        self._maintenance_write(data)
        return data

    def _maintenance_require(self, transaction_id: str) -> dict:
        import time as _time
        transaction_id=str(transaction_id or "").strip()
        current=self._maintenance_read()
        if current is None:
            raise RuntimeError("MAINTENANCE_TRANSACTION_REQUIRED: call bridge.update.begin first")
        if not transaction_id:
            raise RuntimeError("MAINTENANCE_TRANSACTION_REQUIRED: transaction_id is required")
        if current.get("transaction_id") != transaction_id:
            raise RuntimeError("MAINTENANCE_LOCKED: active transaction "+str(current.get("transaction_id") or "unknown"))
        current["expires_at"]=_time.time()+1800.0
        self._maintenance_write(current)
        return current

    def _maintenance_release(self, transaction_id: str) -> None:
        current=self._maintenance_read()
        if current is None:
            return
        if current.get("transaction_id") != str(transaction_id or "").strip():
            raise RuntimeError("MAINTENANCE_LOCKED: cannot release "+str(current.get("transaction_id") or "unknown"))
        try: self.maintenance_lock_file.unlink()
        except FileNotFoundError: pass

    def _begin(self, transaction_id: str) -> dict:
        transaction_id=str(transaction_id or "").strip()
        current=self._maintenance_read()
        if current is not None and current.get("transaction_id")==transaction_id and self.staging.exists():
            self._maintenance_acquire(transaction_id)
            return {"staging":str(self.staging),"base_version":self._version(self.current),"ready":True,"transaction_id":transaction_id,"lock_reused":True}
        self._maintenance_acquire(transaction_id)
        if self.staging.exists():
            shutil.rmtree(self.staging)
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo")
        shutil.copytree(self.current, self.staging, ignore=ignore)
        return {
            "staging": str(self.staging),
            "base_version": self._version(self.current),
            "ready": True,
            "transaction_id": transaction_id,
            "lock_reused": False,
        }

    def _validate(self) -> dict:
        if not (self.staging / "pyproject.toml").exists():
            raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
        knowledge_gate = require_source_knowledge_publishable(
            self.staging
            / "houdini_adapter"
            / "python"
            / "ai_bridge_houdini"
            / "knowledge",
            require_capability_authority=True,
        )
        compile_targets = [self.staging / "src"]
        for adapter_dir in ("houdini_adapter", "blender_adapter", "unreal_adapter"):
            candidate = self.staging / adapter_dir
            if candidate.exists():
                compile_targets.append(candidate)
        compile_run = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", *[str(p) for p in compile_targets]],
            cwd=str(self.staging), capture_output=True, text=True, timeout=60,
        )
        if compile_run.returncode != 0:
            raise RuntimeError("compileall failed: " + (compile_run.stderr or compile_run.stdout)[-3000:])
        test_run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(self.staging / "tests")],
            cwd=str(self.staging),
            env={**os.environ, "PYTHONPATH": str(self.staging / "src")},
            capture_output=True, text=True, timeout=180,
        )
        if test_run.returncode != 0:
            raise RuntimeError("pytest failed: " + (test_run.stdout + "\n" + test_run.stderr)[-5000:])
        return {
            "compile": "PASS",
            "tests": "PASS",
            "pytest_output": test_run.stdout.strip()[-1000:],
            "staging_version": self._version(self.staging),
            "knowledge_publish_gate": knowledge_gate,
        }

    def _update_source(self) -> dict:
        if self.update_config_file.exists():
            try:
                data = json.loads(self.update_config_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("repository"):
                    config = dict(data)
                    config.setdefault("branch", PRODUCT_REF)
                    config.setdefault("base_branch", config["branch"])
                    config.setdefault("manifest_path", "runtime-release.json")
                    config.setdefault("publish_source_mirror", False)
                    # A user Bus may be selected explicitly as a custom mirror, but
                    # legacy implicit Bus bootstrap is never enabled by product code.
                    config["bootstrap_from_bus"] = False
                    return config
            except Exception:
                pass
        return {
            "repository": PRODUCT_REPOSITORY,
            "branch": PRODUCT_REF,
            "base_branch": PRODUCT_REF,
            "manifest_path": "runtime-release.json",
            "publish_source_mirror": False,
            "bootstrap_from_bus": False,
        }

    def _bus_source(self) -> dict:
        remote = load_remote_config(self.data_dir / "remote.json")
        if remote is None:
            raise RuntimeError("GitHub Bus is not configured")
        return {
            "repository": remote.repository,
            "branch": remote.branch,
        }

    def _token(self) -> str:
        token = self.secret_store.get("github_source") or self.secret_store.get("github_bus")
        if not token:
            raise RuntimeError("GitHub credential is unavailable")
        return token

    @staticmethod
    def _contents_url(repository: str, path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in path.strip("/").split("/"))
        return f"https://api.github.com/repos/{repository}/contents/{encoded}"


    @staticmethod
    def _github_headers(token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _publish_source_mirror(self, client: httpx.Client, repository: str, branch: str, token: str, version: str) -> dict:
        headers = self._github_headers(token)
        api = f"https://api.github.com/repos/{repository}"
        ref_url = f"{api}/git/ref/heads/{quote(branch, safe='/')}"
        ref_response = client.get(ref_url, headers=headers)
        if ref_response.status_code != 200:
            raise RuntimeError(f"GitHub source mirror ref failed: HTTP {ref_response.status_code} {ref_response.text[:300]}")
        head_sha = ((ref_response.json().get("object") or {}).get("sha"))
        if not head_sha:
            raise RuntimeError("GitHub source mirror ref is missing head sha")

        commit_response = client.get(f"{api}/git/commits/{head_sha}", headers=headers)
        if commit_response.status_code != 200:
            raise RuntimeError(f"GitHub source mirror commit failed: HTTP {commit_response.status_code} {commit_response.text[:300]}")
        base_tree = ((commit_response.json().get("tree") or {}).get("sha"))
        if not base_tree:
            raise RuntimeError("GitHub source mirror commit is missing tree sha")

        tree_response = client.get(f"{api}/git/trees/{base_tree}", headers=headers, params={"recursive": "1"})
        if tree_response.status_code != 200:
            raise RuntimeError(f"GitHub source mirror tree failed: HTTP {tree_response.status_code} {tree_response.text[:300]}")
        existing = {
            item.get("path")
            for item in (tree_response.json().get("tree") or [])
            if item.get("type") == "blob" and str(item.get("path") or "").startswith("runtime-src/")
        }

        snapshot = self._source_mirror_snapshot(version)
        entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": text}
            for path, text in sorted(snapshot.items())
        ]
        for stale in sorted(existing - set(snapshot)):
            entries.append({"path": stale, "mode": "100644", "type": "blob", "sha": None})

        create_tree = client.post(
            f"{api}/git/trees",
            headers=headers,
            json={"base_tree": base_tree, "tree": entries},
        )
        if create_tree.status_code >= 400:
            raise RuntimeError(f"GitHub source mirror create-tree failed: HTTP {create_tree.status_code} {create_tree.text[:500]}")
        new_tree = create_tree.json().get("sha")
        create_commit = client.post(
            f"{api}/git/commits",
            headers=headers,
            json={
                "message": f"AI Bridge runtime {version} source mirror",
                "tree": new_tree,
                "parents": [head_sha],
            },
        )
        if create_commit.status_code >= 400:
            raise RuntimeError(f"GitHub source mirror commit failed: HTTP {create_commit.status_code} {create_commit.text[:500]}")
        commit_sha = create_commit.json().get("sha")
        update_ref = client.patch(
            f"{api}/git/refs/heads/{quote(branch, safe='/')}",
            headers=headers,
            json={"sha": commit_sha, "force": False},
        )
        if update_ref.status_code >= 400:
            raise RuntimeError(f"GitHub source mirror ref update failed: HTTP {update_ref.status_code} {update_ref.text[:500]}")
        return {"commit": commit_sha, "file_count": len(snapshot), "prefix": "runtime-src/"}

    def _put(self, client: httpx.Client, repository: str, branch: str, path: str, raw: bytes, token: str, message: str) -> str | None:
        headers = self._github_headers(token)
        url = self._contents_url(repository, path)
        existing = client.get(url, headers=headers, params={"ref": branch})
        body = {
            "message": message,
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": branch,
        }
        if existing.status_code == 200:
            payload = existing.json()
            if payload.get("sha"):
                body["sha"] = payload["sha"]
        elif existing.status_code != 404:
            raise RuntimeError(f"GitHub GET {path} failed: HTTP {existing.status_code} {existing.text[:300]}")
        response = client.put(url, headers=headers, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub PUT {path} failed: HTTP {response.status_code} {response.text[:500]}")
        try:
            return (response.json().get("commit") or {}).get("sha")
        except Exception:
            return None

    def _set_version(self, version: str) -> None:
        pyproject = self.staging / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        text, count = re.subn(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', text, count=1)
        if count != 1:
            raise RuntimeError("Unable to update pyproject version")
        pyproject.write_text(text, encoding="utf-8")

        controller = self.staging / "src" / "ai_bridge" / "transport" / "remote_controller.py"
        text = controller.read_text(encoding="utf-8")
        text, count = re.subn(r'"bridge_version":\s*"[^"]+"', f'"bridge_version": "{version}"', text, count=1)
        if count != 1:
            raise RuntimeError("Unable to update presence version")
        controller.write_text(text, encoding="utf-8")

        index = self.staging / "src" / "ai_bridge" / "web" / "templates" / "index.html"
        text = index.read_text(encoding="utf-8")
        text, count = re.subn(r'AI Bridge <small>V[^<]+</small>', f'AI Bridge <small>V{version}</small>', text, count=1)
        if count != 1:
            raise RuntimeError("Unable to update Dashboard version")
        index.write_text(text, encoding="utf-8")

    @staticmethod
    def _runtime_copy_ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in {"__pycache__", ".pytest_cache"} or name.endswith((".pyc", ".pyo")):
                ignored.add(name)
        return ignored

    def _prepare_distribution_runtime(self, source_runtime: Path, destination: Path) -> dict:
        from ai_bridge.deployment.knowledge_pack import build_distribution_knowledge

        source_runtime = Path(source_runtime).resolve()
        destination = Path(destination)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source_runtime, destination, ignore=self._runtime_copy_ignore)

        source_knowledge = (
            source_runtime / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
        )
        target_knowledge = (
            destination / "houdini_adapter" / "python" / "ai_bridge_houdini" / "knowledge"
        )
        if not source_knowledge.is_dir():
            raise RuntimeError(f"Distribution knowledge source missing: {source_knowledge}")
        return build_distribution_knowledge(target_knowledge, source_root=source_knowledge)

    def _build_bundle(self, target: Path) -> None:
        import zipfile
        if target.exists():
            target.unlink()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(self.staging.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.staging)
                if "__pycache__" in rel.parts or ".pytest_cache" in rel.parts or path.suffix in {".pyc", ".pyo"}:
                    continue
                zf.write(path, (Path("runtime") / rel).as_posix())

    def _build_distribution_bundle(self, target: Path) -> dict:
        import tempfile
        import zipfile

        if target.exists():
            target.unlink()
        with tempfile.TemporaryDirectory(prefix="ai_bridge_distribution_") as temp:
            runtime = Path(temp) / "runtime"
            knowledge = self._prepare_distribution_runtime(self.staging, runtime)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for path in sorted(runtime.rglob("*")):
                    if path.is_file():
                        zf.write(path, (Path("runtime") / path.relative_to(runtime)).as_posix())
        return knowledge

    @staticmethod
    def _supervisor_version_from_source(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        match = re.search(r'(?m)^SUPERVISOR_VERSION\s*=\s*["\']([^"\']+)["\']', text)
        if not match:
            raise RuntimeError("Supervisor version constant not found")
        return match.group(1).strip()

    def _validate_installer_tree(self, package_root: Path) -> dict:
        from ai_bridge.deployment.knowledge_pack import validate_distribution

        required = [
            package_root / "INSTALL_AI_BRIDGE.bat",
            package_root / "AI_Bridge.bat",
            package_root / "START_AI_BRIDGE.bat",
            package_root / "_System" / "bootstrap.bat",
            package_root / "_System" / "supervisor.py",
            package_root / "Runtime" / "Current" / "pyproject.toml",
            package_root / "Runtime" / "Current" / "launch_bridge.py",
        ]
        missing = [str(path.relative_to(package_root)) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Installer tree missing: " + ", ".join(missing))

        forbidden_paths = [
            package_root / ".ai_bridge",
            package_root / "Runtime" / "Versions",
            package_root / "Runtime" / "Staging",
            package_root / "Runtime" / "Previous",
        ]
        present_forbidden = [str(path.relative_to(package_root)) for path in forbidden_paths if path.exists()]
        if present_forbidden:
            raise RuntimeError("Installer contains generated state: " + ", ".join(present_forbidden))

        all_paths = [path.relative_to(package_root).as_posix() for path in package_root.rglob("*")]
        backup_paths = [
            path for path in all_paths
            if any(marker in Path(path).name.lower() for marker in BOOTSTRAP_BACKUP_NAME_MARKERS)
        ]
        if backup_paths:
            raise RuntimeError("Installer contains historical bootstrap backups: " + ", ".join(backup_paths[:10]))

        knowledge_root = (
            package_root / "Runtime" / "Current" / "houdini_adapter"
            / "python" / "ai_bridge_houdini" / "knowledge"
        )
        knowledge = validate_distribution(knowledge_root)
        if not knowledge.get("ok"):
            raise RuntimeError("Installer knowledge is not clean: " + "; ".join(knowledge.get("violations") or []))

        supervisor_version = self._supervisor_version_from_source(package_root / "_System" / "supervisor.py")
        return {
            "ok": True,
            "supervisor_version": supervisor_version,
            "runtime_version": self._version(package_root / "Runtime" / "Current"),
            "knowledge_digest": knowledge["content_digest"],
            "file_count": sum(1 for path in package_root.rglob("*") if path.is_file()),
        }

    def _build_clean_installer_archive(self, target: Path) -> dict:
        import tempfile
        import zipfile

        target = Path(target)
        if target.exists():
            target.unlink()

        with tempfile.TemporaryDirectory(prefix="ai_bridge_installer_") as temp:
            package_root = Path(temp) / "AI_Bridge"
            package_root.mkdir(parents=True)

            inventory = self._bootstrap_inventory(include_text=False, include_backups=False)
            for item in inventory["files"]:
                source = self.root / str(item["path"])
                destination = package_root / str(item["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            supervisor_version = self._supervisor_version_from_source(package_root / "_System" / "supervisor.py")
            (package_root / "_System" / "VERSION.txt").write_text(
                f"AI Bridge Supervisor {supervisor_version}\nStable shell for replaceable Runtime {self._version(self.current)}+\n",
                encoding="utf-8",
            )

            runtime_knowledge = self._prepare_distribution_runtime(
                self.current,
                package_root / "Runtime" / "Current",
            )

            (package_root / "INSTALL_AI_BRIDGE.bat").write_text(
                "@echo off\r\n"
                "setlocal EnableExtensions\r\n"
                "cd /d \"%~dp0\"\r\n"
                "echo ==========================================\r\n"
                "echo AI Bridge Clean Installer\r\n"
                "echo ==========================================\r\n"
                "echo This package starts with no project state.\r\n"
                "call \"AI_Bridge.bat\"\r\n"
                "exit /b %errorlevel%\r\n",
                encoding="utf-8",
            )
            (package_root / "INSTALL_README.txt").write_text(
                "AI Bridge Clean Installer\n"
                "=========================\n\n"
                "1. Extract the AI_Bridge folder to a writable local directory.\n"
                "2. Double-click INSTALL_AI_BRIDGE.bat.\n"
                "3. First run opens Setup. Choose 'one-click create clean Bus and connect'.\n"
                "4. Install the Houdini host plugin from the Dashboard when desired.\n\n"
                "The package contains no Bridge ID, GitHub token, Workspace, Session, project file, command history, or recovery state.\n",
                encoding="utf-8",
            )

            validation = self._validate_installer_tree(package_root)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for path in sorted(package_root.rglob("*")):
                    if path.is_file():
                        zf.write(path, (Path("AI_Bridge") / path.relative_to(package_root)).as_posix())

        raw = target.read_bytes()
        return {
            "archive": str(target),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "supervisor_version": validation["supervisor_version"],
            "runtime_version": validation["runtime_version"],
            "knowledge_digest": runtime_knowledge["content_digest"],
            "validation": validation,
        }

    def _publish_clean_installer(self) -> dict:
        source = self._update_source()
        repository = str(source["repository"])
        branch = str(source.get("branch") or PRODUCT_REF)
        token = self._token()
        runtime_version = self._version(self.current)
        archive = self.data_dir / f"AI_Bridge_Clean_Installer_{runtime_version}.zip"
        build = self._build_clean_installer_archive(archive)
        if build["size"] > 90 * 1024 * 1024:
            raise RuntimeError("Installer exceeds GitHub Contents API safety limit")

        artifact_path = f"installer/AI_Bridge_Clean_Installer_{runtime_version}.zip"
        release_path = "installer/installer-release.json"
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=8.0)) as client:
                artifact_commit = self._put(
                    client, repository, branch, artifact_path, archive.read_bytes(), token,
                    f"AI Bridge clean installer {runtime_version}",
                )
                release = {
                    "schema_version": "1.0",
                    "runtime_version": runtime_version,
                    "supervisor_version": build["supervisor_version"],
                    "artifact_path": artifact_path,
                    "sha256": build["sha256"],
                    "size": build["size"],
                    "runtime_update_manifest": "distribution-release.json",
                    "knowledge_digest": build["knowledge_digest"],
                    "validation": build["validation"],
                }
                release_commit = self._put(
                    client, repository, branch, release_path,
                    (json.dumps(release, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    token,
                    f"AI Bridge clean installer {runtime_version} manifest",
                )
        finally:
            try:
                archive.unlink()
            except FileNotFoundError:
                pass

        return {
            "published": True,
            "repository": repository,
            "branch": branch,
            "artifact_path": artifact_path,
            "artifact_commit": artifact_commit,
            "release_path": release_path,
            "release_commit": release_commit,
            **build,
        }

    def _publish_via_staging_worker(self, version: str, notes: str) -> dict:
        if not self.staging.is_dir():
            raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
        request = {
            "root": str(self.root),
            "data_dir": str(self.data_dir),
            "staging": str(self.staging),
            "version": str(version),
            "notes": str(notes),
        }
        env = dict(os.environ)
        staging_src = str((self.staging / "src").resolve())
        prior_pythonpath = str(env.get("PYTHONPATH") or "")
        env["PYTHONPATH"] = (
            staging_src
            if not prior_pythonpath
            else staging_src + os.pathsep + prior_pythonpath
        )
        env["AI_BRIDGE_ROOT"] = str(self.root)
        run = subprocess.run(
            [sys.executable, "-m", "ai_bridge.deployment.publisher_worker"],
            cwd=str(self.staging),
            env=env,
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if run.returncode != 0:
            detail = (run.stderr or run.stdout or "publisher worker failed")[-5000:]
            raise RuntimeError("staging publisher failed: " + detail)
        try:
            payload = json.loads(run.stdout)
        except Exception as exc:
            raise RuntimeError(
                "staging publisher returned invalid JSON: " + run.stdout[-2000:]
            ) from exc
        authority = payload.get("publisher_authority")
        if not isinstance(authority, dict):
            raise RuntimeError("staging publisher omitted publisher_authority")
        if str(authority.get("source_version") or "") != str(version):
            raise RuntimeError("staging publisher version authority mismatch")
        if Path(str(authority.get("staging") or "")).resolve() != self.staging.resolve():
            raise RuntimeError("staging publisher source root mismatch")
        return payload

    def _publish_artifacts_in_process(self, version: str, notes: str) -> dict:
        if self._version(self.staging) != str(version):
            raise RuntimeError(
                f"staging version mismatch: expected {version}, got {self._version(self.staging)}"
            )
        validation = self._validate()
        source = self._update_source()
        repository = str(source["repository"])
        branch = str(source.get("branch") or PRODUCT_REF)
        token = self._token()
        bundle = self.data_dir / "runtime_publish.zip"
        distribution_bundle = self.data_dir / "runtime_distribution_publish.zip"
        self._build_bundle(bundle)
        distribution_knowledge = self._build_distribution_bundle(distribution_bundle)
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=8.0)) as client:
                bundle_commit = self._put(
                    client, repository, branch, "runtime_bundle.zip", bundle.read_bytes(), token,
                    f"AI Bridge runtime {version} bundle",
                )
                distribution_bundle_commit = self._put(
                    client, repository, branch, "runtime_distribution_bundle.zip", distribution_bundle.read_bytes(), token,
                    f"AI Bridge clean runtime {version} bundle",
                )
                source_mirror = None
                if bool(source.get("publish_source_mirror", False)):
                    source_mirror = self._publish_source_mirror(
                        client, repository, branch, token, version
                    )
                manifest = {
                    "version": version,
                    "bundle_path": "runtime_bundle.zip",
                    "runtime_path": "runtime",
                    "min_supervisor_version": "0.1.0",
                    "channel": "stable",
                    "notes": notes,
                }
                if source_mirror is not None:
                    manifest["source_prefix"] = source_mirror["prefix"]
                    manifest["source_commit"] = source_mirror["commit"]
                manifest_commit = self._put(
                    client, repository, branch, str(source.get("manifest_path") or "runtime-release.json"),
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"), token,
                    f"AI Bridge runtime {version} manifest",
                )
                distribution_manifest = {
                    "version": version,
                    "bundle_path": "runtime_distribution_bundle.zip",
                    "runtime_path": "runtime",
                    "min_supervisor_version": "0.1.2",
                    "channel": "stable-clean",
                    "knowledge": {
                        "mode": "clean_distribution",
                        "content_digest": distribution_knowledge["content_digest"],
                        "included_recipes": distribution_knowledge["included_recipes"],
                        "excluded_recipes": distribution_knowledge["excluded_recipes"],
                    },
                    "notes": notes,
                }
                if source_mirror is not None:
                    distribution_manifest["source_prefix"] = source_mirror["prefix"]
                    distribution_manifest["source_commit"] = source_mirror["commit"]
                distribution_manifest_commit = self._put(
                    client, repository, branch, "distribution-release.json",
                    json.dumps(distribution_manifest, ensure_ascii=False, indent=2).encode("utf-8"), token,
                    f"AI Bridge clean runtime {version} manifest",
                )
        finally:
            for path in (bundle, distribution_bundle):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self.update_check_file.write_text("check now", encoding="utf-8")
        return {
            "published": True,
            "version": version,
            "repository": repository,
            "branch": branch,
            "bundle_commit": bundle_commit,
            "manifest_commit": manifest_commit,
            "distribution_bundle_commit": distribution_bundle_commit,
            "distribution_manifest_commit": distribution_manifest_commit,
            "distribution_knowledge": distribution_knowledge,
            "source_mirror": source_mirror,
            "validation": validation,
            "supervisor_check_requested": True,
        }

    def _publish(self, version: str, notes: str) -> dict:
        current_version = self._version_tuple(self._version(self.current))
        target_version = self._version_tuple(version)
        if target_version <= current_version:
            raise ValueError(
                f"new version must be greater than current {self._version(self.current)}"
            )
        if not self.staging.is_dir():
            raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
        self._set_version(version)
        return self._publish_via_staging_worker(version, notes)

    def _supervisor_upgrade_status(self, recovery_id: str | None = None) -> dict:
        supervisor_source = self.root / "_System" / "supervisor.py"
        source_version = (
            self._supervisor_version_from_source(supervisor_source)
            if supervisor_source.is_file()
            else None
        )
        status = {}
        try:
            status = json.loads(self.status_file.read_text(encoding="utf-8"))
        except Exception:
            pass

        result = None
        result_path = None
        if recovery_id:
            candidate = self.data_dir / f"supervisor_upgrade_result_{recovery_id}.json"
            if candidate.is_file():
                result_path = candidate
        else:
            candidates = sorted(
                self.data_dir.glob("supervisor_upgrade_result_*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            if candidates:
                result_path = candidates[0]
        if result_path is not None:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                result = {
                    "status": "result_unreadable",
                    "path": str(result_path),
                }

        return {
            "source_version": source_version,
            "running_version": status.get("supervisor_version"),
            "state": status.get("state"),
            "pid": status.get("pid"),
            "runtime_version": status.get("runtime_version"),
            "runtime_current": status.get("runtime_current"),
            "upgrade_protocol": status.get("supervisor_update_protocol"),
            "recovery_id": recovery_id,
            "upgrade_result": result,
        }

    def _stage_supervisor_upgrade(self, args: dict) -> dict:
        source = self._update_source()
        repository = str(source["repository"])
        branch = str(source.get("branch") or PRODUCT_REF)
        release_doc = self._fetch_repo_text(
            repository,
            branch,
            "supervisor-release.json",
            timeout_seconds=float(args.get("authority_timeout_seconds") or 8.0),
        )
        manifest = json.loads(release_doc["text"])
        if not isinstance(manifest, dict):
            raise RuntimeError("Supervisor release manifest must be an object")

        target_version = str(manifest.get("version") or "").strip()
        current_source = self.root / "_System" / "supervisor.py"
        current_version = self._supervisor_version_from_source(current_source)
        if args.get("expected_current_version") is not None:
            expected = str(args.get("expected_current_version") or "").strip()
            if expected != current_version:
                raise RuntimeError(
                    f"SUPERVISOR_VERSION_CONFLICT: expected {expected}, current {current_version}"
                )
        if self._version_tuple(target_version) < self._version_tuple(current_version):
            raise RuntimeError(
                f"Supervisor release {target_version} is older than current {current_version}"
            )
        if target_version == current_version:
            return {
                "status": "already_current",
                "current_version": current_version,
                "target_version": target_version,
                "manifest_sha": release_doc.get("sha"),
            }

        min_runtime = str(manifest.get("min_runtime_version") or "").strip()
        runtime_version = self._version(self.current)
        if min_runtime and self._version_tuple(runtime_version) < self._version_tuple(min_runtime):
            raise RuntimeError(
                f"SUPERVISOR_RUNTIME_TOO_OLD: requires {min_runtime}, current {runtime_version}"
            )

        recovery_id = "sup_" + hashlib.sha256(
            f"{time.time_ns()}|{os.getpid()}|{target_version}".encode("utf-8")
        ).hexdigest()[:16]
        upgrade_root = (
            self.root
            / "_System"
            / "SupervisorUpgrade"
            / target_version
            / recovery_id
        )
        payload_root = upgrade_root / "payload"
        backup_root = upgrade_root / "backup"
        payload_root.mkdir(parents=True, exist_ok=True)
        backup_root.mkdir(parents=True, exist_ok=True)

        allowed_targets = {
            "_System/supervisor.py",
            "_System/VERSION.txt",
        }
        prepared = []
        for index, item in enumerate(manifest.get("files") or []):
            if not isinstance(item, dict):
                raise RuntimeError(f"Supervisor release files[{index}] must be an object")
            target_rel = str(item.get("target") or "").replace("\\", "/").lstrip("/")
            source_path = str(item.get("source_path") or "").strip()
            expected_git_sha = str(item.get("github_sha") or "").strip()
            if target_rel not in allowed_targets:
                raise RuntimeError(f"Supervisor release target not allowed: {target_rel}")
            if not source_path.startswith(f"bootstrap/supervisor/{target_version}/"):
                raise RuntimeError(
                    f"Supervisor release source path outside version authority: {source_path}"
                )
            file_doc = self._fetch_repo_text(
                repository,
                branch,
                source_path,
                timeout_seconds=float(args.get("authority_timeout_seconds") or 8.0),
            )
            if not expected_git_sha or str(file_doc.get("sha") or "") != expected_git_sha:
                raise RuntimeError(
                    f"Supervisor release Git blob mismatch: {source_path}"
                )
            raw = file_doc["text"].encode("utf-8")
            actual_sha = hashlib.sha256(raw).hexdigest()

            staged = payload_root / target_rel
            target = self.root / target_rel
            backup = backup_root / target_rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            backup.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(raw)
            target_existed = target.is_file()
            if target_existed:
                shutil.copy2(target, backup)
            prepared.append({
                "target_relative": target_rel,
                "source_path": source_path,
                "sha256": actual_sha,
                "github_sha": expected_git_sha,
                "staged": str(staged),
                "target": str(target),
                "backup": str(backup),
                "target_existed": target_existed,
            })

        if not any(item["target_relative"] == "_System/supervisor.py" for item in prepared):
            raise RuntimeError("Supervisor release must include _System/supervisor.py")
        staged_supervisor = payload_root / "_System" / "supervisor.py"
        staged_version = self._supervisor_version_from_source(staged_supervisor)
        if staged_version != target_version:
            raise RuntimeError(
                f"Supervisor staged version mismatch: {staged_version} != {target_version}"
            )

        request_path = self.data_dir / f"supervisor_upgrade_request_{recovery_id}.json"
        result_path = self.data_dir / f"supervisor_upgrade_result_{recovery_id}.json"
        request = {
            "schema_version": "1.0",
            "recovery_id": recovery_id,
            "root": str(self.root),
            "data_dir": str(self.data_dir),
            "current_version": current_version,
            "target_version": target_version,
            "runtime_version": runtime_version,
            "manifest_sha": release_doc.get("sha"),
            "files": prepared,
            "delay_seconds": max(
                2.0,
                min(float(args.get("delay_seconds") or 3.0), 8.0),
            ),
            "stop_timeout_seconds": max(
                8.0,
                min(float(args.get("stop_timeout_seconds") or 20.0), 60.0),
            ),
            "health_timeout_seconds": max(
                10.0,
                min(float(args.get("health_timeout_seconds") or 30.0), 90.0),
            ),
            "result_path": str(result_path),
        }
        _write_json = lambda path, payload: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.with_suffix(path.suffix + ".tmp").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            ),
            path.with_suffix(path.suffix + ".tmp").replace(path),
        )
        _write_json(request_path, request)

        env = dict(os.environ)
        runtime_src = str((self.current / "src").resolve())
        prior_pythonpath = str(env.get("PYTHONPATH") or "")
        env["PYTHONPATH"] = (
            runtime_src
            if not prior_pythonpath
            else runtime_src + os.pathsep + prior_pythonpath
        )
        env["AI_BRIDGE_ROOT"] = str(self.root)
        kwargs = {
            "cwd": str(self.current),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            )
        else:
            kwargs["start_new_session"] = True
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_bridge.deployment.supervisor_upgrade_worker",
                "--request",
                str(request_path),
            ],
            **kwargs,
        )
        return {
            "status": "scheduled",
            "recovery_id": recovery_id,
            "current_version": current_version,
            "target_version": target_version,
            "runtime_version": runtime_version,
            "worker_pid": int(worker.pid),
            "request_path": str(request_path),
            "result_path": str(result_path),
            "manifest_sha": release_doc.get("sha"),
            "prepared_files": [
                {
                    "target": item["target_relative"],
                    "sha256": item["sha256"],
                }
                for item in prepared
            ],
        }

    def _stage_houdini_adapter(self) -> dict:
        runtime_root = Path(__file__).resolve().parents[3]
        installer_path = runtime_root / "install_houdini_adapter.py"
        if not installer_path.exists():
            raise RuntimeError(f"Houdini installer missing: {installer_path}")

        spec = importlib.util.spec_from_file_location(
            "ai_bridge_runtime_houdini_installer",
            installer_path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load Houdini installer module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        user_dir = module.default_houdini_user_dir()
        result = module.install_adapter(
            user_dir,
            source=runtime_root / "houdini_adapter",
            running_safe=True,
        )
        result = dict(result)
        result.update(
            {
                "runtime_version": self._version(runtime_root),
                "adapter_source": str(runtime_root / "houdini_adapter"),
                "restart_required": bool(result.get("changed")),
                "hot_reload_performed": False,
            }
        )
        return result

    def execute(self, command: CommandEnvelope) -> ExecutionResult:
        try:
            op = command.operation
            args = command.arguments
            if op == "bridge.bootstrap.inventory":
                return self._success(command.command_id, self._bootstrap_inventory(
                    include_text=bool(args.get("include_text", False)),
                    include_backups=bool(args.get("include_backups", False)),
                    max_chars_per_file=int(args.get("max_chars_per_file") or 40000),
                ))
            if op == "bridge.bootstrap.build_installer":
                return self._success(command.command_id, self._publish_clean_installer())
            if op == "bridge.project.resume":
                return self._project_resume(command.command_id, dict(args))
            if op == "bridge.update.status":
                supervisor = {}
                try:
                    supervisor = json.loads(self.status_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
                return self._success(command.command_id, {
                    "current_version": self._version(self.current),
                    "staging_version": self._version(self.staging) if self.staging.exists() else None,
                    "staging_exists": self.staging.exists(),
                    "supervisor": supervisor,
                    "update_source": self._update_source(),
                    "maintenance": self._maintenance_read(),
                    "dev_acceleration": {
                        "read_files": True,
                        "write_files": True,
                        "test_subset": True,
                        "source_mirror_prefix": "runtime-src/",
                    },
                })
            if op == "bridge.update.begin":
                transaction_id=str(args.get("transaction_id") or command.command_id)
                return self._success(command.command_id,self._begin(transaction_id),rollback=True)
            if op == "bridge.update.read_files":
                source=str(args.get("source") or "staging").strip().lower()
                transaction_id=str(args.get("transaction_id") or "")
                if source == "staging":
                    self._maintenance_require(transaction_id)
                payload=self._read_files(
                    args.get("paths") or [],
                    source=source,
                    max_chars_per_file=int(args.get("max_chars_per_file") or 40000),
                )
                if transaction_id:
                    payload["transaction_id"]=transaction_id
                return self._success(command.command_id,payload)
            if op == "bridge.update.write_file":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                target=self._safe_staging_path(str(args.get("path") or ""))
                text=args.get("text")
                if not isinstance(text,str):
                    raise ValueError("arguments.text must be a UTF-8 string")
                if not self.staging.exists():
                    raise RuntimeError("Staging is not initialized; call bridge.update.begin first")
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(text,encoding="utf-8")
                return self._success(command.command_id,{"path":str(target.relative_to(self.staging)),"chars":len(text),"transaction_id":transaction_id},rollback=True)
            if op == "bridge.update.write_files":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                payload=self._write_files(args.get("files") or [])
                payload["transaction_id"]=transaction_id
                return self._success(command.command_id,payload,rollback=True)
            if op == "bridge.update.delete_file":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                target=self._safe_staging_path(str(args.get("path") or ""))
                existed=target.exists()
                if target.is_dir(): shutil.rmtree(target)
                else:
                    try: target.unlink()
                    except FileNotFoundError: pass
                return self._success(command.command_id,{"path":str(target.relative_to(self.staging)),"existed":existed,"transaction_id":transaction_id},rollback=True)
            if op == "bridge.update.test_subset":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                payload=self._test_subset(
                    args.get("paths") or [],
                    k=args.get("k"),
                    timeout_sec=int(args.get("timeout_sec") or 90),
                )
                payload["transaction_id"]=transaction_id
                return self._success(command.command_id,payload)
            if op == "bridge.update.validate":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                payload=self._validate(); payload["transaction_id"]=transaction_id
                return self._success(command.command_id,payload)
            if op == "bridge.update.publish":
                transaction_id=str(args.get("transaction_id") or "")
                self._maintenance_require(transaction_id)
                version=str(args.get("version") or "").strip()
                notes=str(args.get("notes") or "")
                payload=self._publish(version,notes)
                self._maintenance_release(transaction_id)
                payload["transaction_id"]=transaction_id
                return self._success(command.command_id,payload,rollback=True)
            if op == "bridge.supervisor.status":
                return self._success(
                    command.command_id,
                    self._supervisor_upgrade_status(
                        str(args.get("recovery_id") or "").strip() or None
                    ),
                )
            if op == "bridge.supervisor.upgrade":
                return self._success(
                    command.command_id,
                    self._stage_supervisor_upgrade(dict(args)),
                    rollback=True,
                )
            if op == "bridge.adapter.stage_houdini":
                return self._success(command.command_id, self._stage_houdini_adapter())
            if op == "bridge.plugin.status":
                payload = (
                    self.plugin_status_provider()
                    if self.plugin_status_provider is not None
                    else self.plugins.status()
                )
                return self._success(command.command_id, payload)
            if op == "bridge.plugin.install":
                host_id = str(args.get("host_id") or "").strip().lower()
                if not host_id:
                    raise ValueError("host_id is required")
                payload = (
                    self.plugin_install_handler(host_id)
                    if self.plugin_install_handler is not None
                    else self.plugins.install(
                        host_id,
                        host_running=bool(args.get("host_running", True)),
                    )
                )
                return self._success(command.command_id, payload)
            if op == "bridge.plugin.install_all":
                payload = (
                    self.plugin_install_all_handler()
                    if self.plugin_install_all_handler is not None
                    else self.plugins.install_all(running_safe_default=True)
                )
                return self._success(command.command_id, payload)
            if op == "bridge.plugin.restart_apply":
                if self.plugin_restart_handler is None:
                    raise RuntimeError("PLUGIN_RESTART_HANDLER_UNAVAILABLE")
                return self._handler_outcome(
                    command.command_id,
                    self.plugin_restart_handler(dict(args)),
                    success_statuses={"applied", "already_current"},
                    failure_code="PLUGIN_RESTART_NOT_APPLIED",
                    rollback=True,
                )
            if op == "bridge.host.force_recover":
                if self.host_recover_handler is None:
                    raise RuntimeError("HOST_RECOVER_HANDLER_UNAVAILABLE")
                return self._handler_outcome(
                    command.command_id,
                    self.host_recover_handler(dict(args)),
                    success_statuses={"recovered", "restarted_checkpoint_safe", "recovered_saved_project"},
                    failure_code="HOST_RECOVERY_NOT_COMPLETED",
                    rollback=True,
                )
            return self._failure(command.command_id, "UNSUPPORTED_BRIDGE_ADMIN_OPERATION", op, FailureOrigin.ADAPTER)
        except Exception as exc:
            return self._failure(command.command_id, "BRIDGE_ADMIN_FAILED", f"{type(exc).__name__}: {exc}")
