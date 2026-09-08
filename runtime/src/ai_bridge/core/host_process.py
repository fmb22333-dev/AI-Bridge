from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from pathlib import Path


class HostProcessController:
    """Minimal OS process control used only after an explicit recovery policy fires."""

    def executable_path(self, pid: int) -> str:
        pid = int(pid)
        if pid <= 0:
            raise ValueError("invalid host pid")

        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                raise RuntimeError(f"unable to open host process {pid}")
            try:
                size = ctypes.c_ulong(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
                if not ok:
                    raise RuntimeError(f"unable to query executable for host process {pid}")
                return buffer.value
            finally:
                kernel32.CloseHandle(handle)

        proc_exe = Path(f"/proc/{pid}/exe")
        if proc_exe.exists():
            return os.readlink(proc_exe)
        raise RuntimeError(f"host executable discovery is not supported for pid {pid} on this platform")

    @staticmethod
    def _windows_process_is_running(pid: int) -> bool:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                raise RuntimeError(f"unable to query exit code for host process {pid}")
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    def is_running(self, pid: int) -> bool:
        try:
            pid = int(pid)
            if pid <= 0:
                return False
            if os.name == "nt":
                return self._windows_process_is_running(pid)
            os.kill(pid, 0)
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def close_gracefully(self, pid: int, *, wait_seconds: float = 8.0, force_if_needed: bool = True) -> dict:
        pid = int(pid)
        started = time.monotonic()
        requested = False
        force_used = False
        detail = None

        if not self.is_running(pid):
            return {"pid": pid, "close_requested": False, "force_used": False, "terminated": True, "elapsed_ms": 0.0}

        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            requested = completed.returncode == 0
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip() or None
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                requested = True
            except ProcessLookupError:
                requested = False

        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while self.is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.1)

        if self.is_running(pid) and force_if_needed:
            force_used = True
            forced = self.terminate(pid)
            if not forced.get("terminated"):
                raise RuntimeError(f"host process {pid} did not terminate")

        return {
            "pid": pid,
            "close_requested": requested,
            "force_used": force_used,
            "terminated": not self.is_running(pid),
            "detail": detail,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
        }

    def terminate(self, pid: int, *, force_after_seconds: float = 1.0) -> dict:
        pid = int(pid)
        started = time.monotonic()
        if not self.is_running(pid):
            return {
                "pid": pid,
                "terminated": True,
                "already_stopped": True,
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            }

        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=max(5.0, float(force_after_seconds) + 5.0),
                check=False,
            )
            if completed.returncode != 0 and self.is_running(pid):
                detail = (completed.stderr or completed.stdout or "taskkill failed").strip()
                raise RuntimeError(detail)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + max(0.0, float(force_after_seconds))
            while self.is_running(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if self.is_running(pid):
                os.kill(pid, signal.SIGKILL)

        return {
            "pid": pid,
            "terminated": not self.is_running(pid),
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
        }

    def launch(self, executable: str, project_file: str) -> dict:
        executable = str(executable or "").strip()
        project_file = str(project_file or "").strip()
        if not executable:
            raise ValueError("host executable is required")
        if not project_file:
            raise ValueError("project file is required")
        process = subprocess.Popen(
            [executable, project_file],
            cwd=str(Path(project_file).expanduser().parent),
            close_fds=True,
        )
        return {"pid": process.pid, "executable": executable, "project_file": project_file}
