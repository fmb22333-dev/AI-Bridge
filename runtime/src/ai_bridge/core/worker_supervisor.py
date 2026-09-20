from __future__ import annotations

import os
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock, Thread

from ai_bridge.core.worker_manifest import ServiceSpec


class WorkerError(RuntimeError):
    code = "WORKER_ERROR"


class WorkerAlreadyRunning(WorkerError):
    code = "SERVICE_ALREADY_RUNNING"


class WorkerNotRunning(WorkerError):
    code = "SERVICE_NOT_RUNNING"


class WorkerReloadManual(WorkerError):
    code = "SERVICE_RELOAD_MANUAL"


@dataclass
class _WorkerRecord:
    workspace_id: str
    service_name: str
    process: subprocess.Popen
    spec: ServiceSpec
    root: Path
    logs: deque[str]
    log_thread: Thread | None = None
    lock: RLock = field(default_factory=RLock)
    stopped: bool = False


class WorkerSupervisor:
    def __init__(self, *, max_log_lines: int = 1000) -> None:
        self.max_log_lines = max(16, min(int(max_log_lines), 10000))
        self._items: dict[tuple[str, str], _WorkerRecord] = {}
        self._lock = RLock()

    @staticmethod
    def _resolve_cwd(root: Path, relative: str) -> Path:
        root = Path(root).expanduser().resolve()
        candidate = (root / relative).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("WORKSPACE_PATH_ESCAPE") from exc
        if not candidate.exists():
            raise FileNotFoundError(f"service cwd does not exist: {candidate}")
        if not candidate.is_dir():
            raise NotADirectoryError(str(candidate))
        return candidate

    @staticmethod
    def _reader(record: _WorkerRecord) -> None:
        stream = record.process.stdout
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                with record.lock:
                    record.logs.append(line.rstrip("\r\n"))
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _snapshot(record: _WorkerRecord) -> dict:
        exit_code = record.process.poll()
        if exit_code is None:
            state = "running"
        elif record.stopped:
            state = "stopped"
        else:
            state = "exited"
        return {
            "workspace": record.workspace_id,
            "service": record.service_name,
            "state": state,
            "pid": int(record.process.pid),
            "exit_code": exit_code,
            "reload": record.spec.reload,
        }

    @staticmethod
    def _windows_taskkill(pid: int, *, force: bool) -> bool:
        argv = ["taskkill", "/PID", str(int(pid)), "/T"]
        if force:
            argv.append("/F")
        try:
            run = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return run.returncode == 0

    def start(
        self,
        workspace_id: str,
        workspace_root: Path,
        service_name: str,
        spec: ServiceSpec,
    ) -> dict:
        key = (str(workspace_id), str(service_name))
        with self._lock:
            existing = self._items.get(key)
            if existing is not None and existing.process.poll() is None:
                raise WorkerAlreadyRunning(service_name)

            cwd = self._resolve_cwd(Path(workspace_root), spec.cwd)
            env = dict(os.environ)
            env.update(spec.env)
            process = subprocess.Popen(
                list(spec.argv),
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
            record = _WorkerRecord(
                workspace_id=str(workspace_id),
                service_name=str(service_name),
                process=process,
                spec=spec,
                root=Path(workspace_root).resolve(),
                logs=deque(maxlen=self.max_log_lines),
            )
            thread = Thread(
                target=self._reader,
                args=(record,),
                name=f"ai-bridge-worker-log-{service_name}",
                daemon=True,
            )
            record.log_thread = thread
            self._items[key] = record
            thread.start()
            return self._snapshot(record)

    def status(self, workspace_id: str, service_name: str) -> dict:
        key = (str(workspace_id), str(service_name))
        with self._lock:
            record = self._items.get(key)
            if record is None:
                return {
                    "workspace": str(workspace_id),
                    "service": str(service_name),
                    "state": "stopped",
                    "pid": None,
                    "exit_code": None,
                }
            return self._snapshot(record)

    def logs(self, workspace_id: str, service_name: str, *, tail: int = 200) -> dict:
        key = (str(workspace_id), str(service_name))
        with self._lock:
            record = self._items.get(key)
            if record is None:
                lines: list[str] = []
                status = self.status(workspace_id, service_name)
            else:
                tail = max(1, min(int(tail), self.max_log_lines))
                with record.lock:
                    lines = list(record.logs)[-tail:]
                status = self._snapshot(record)
        return {**status, "lines": lines}

    def stop(self, workspace_id: str, service_name: str) -> dict:
        key = (str(workspace_id), str(service_name))
        with self._lock:
            record = self._items.get(key)
            if record is None or record.process.poll() is not None:
                raise WorkerNotRunning(service_name)

        process = record.process
        if os.name == "nt":
            if not self._windows_taskkill(process.pid, force=False):
                process.terminate()
        else:
            process.terminate()
        try:
            process.wait(timeout=float(record.spec.stop_timeout_seconds))
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                if not self._windows_taskkill(process.pid, force=True):
                    process.kill()
            else:
                process.kill()
            process.wait(timeout=max(1.0, float(record.spec.stop_timeout_seconds)))
        with record.lock:
            record.stopped = True

        return self._snapshot(record)

    def restart(
        self,
        workspace_id: str,
        workspace_root: Path,
        service_name: str,
        spec: ServiceSpec,
    ) -> dict:
        current = self.status(workspace_id, service_name)
        if current["state"] == "running":
            self.stop(workspace_id, service_name)
        return self.start(workspace_id, workspace_root, service_name, spec)

    def reload(
        self,
        workspace_id: str,
        workspace_root: Path,
        service_name: str,
        spec: ServiceSpec,
    ) -> dict:
        if spec.reload == "manual":
            raise WorkerReloadManual(service_name)
        if spec.reload == "native":
            status = self.status(workspace_id, service_name)
            if status["state"] != "running":
                raise WorkerNotRunning(service_name)
            return {**status, "action": "native"}
        restarted = self.restart(workspace_id, workspace_root, service_name, spec)
        return {**restarted, "action": "restart"}

    def close(self) -> None:
        with self._lock:
            keys = list(self._items)
        for workspace_id, service_name in keys:
            try:
                if self.status(workspace_id, service_name)["state"] == "running":
                    self.stop(workspace_id, service_name)
            except Exception:
                pass
