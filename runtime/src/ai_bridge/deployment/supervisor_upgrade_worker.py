from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


class SupervisorUpgradeError(RuntimeError):
    pass


DETACHED_WORKER_ENV = "AI_BRIDGE_SUPERVISOR_UPGRADE_DETACHED_CHILD"


def _spawn_detached_generation(request_path: Path) -> int:
    env = dict(os.environ)
    env[DETACHED_WORKER_ENV] = "1"
    kwargs = {
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
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_bridge.deployment.supervisor_upgrade_worker",
            "--request",
            str(Path(request_path).resolve()),
        ],
        **kwargs,
    )
    return int(proc.pid)



def _write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _wait_for_supervisor_exit(pid_file: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + max(1.0, float(timeout))
    original_pid = _read_pid(pid_file)
    while time.monotonic() < deadline:
        current = _read_pid(pid_file)
        if current is None:
            return
        if original_pid and current != original_pid:
            return
        if original_pid and not _process_alive(original_pid):
            try:
                pid_file.unlink()
            except FileNotFoundError:
                pass
            return
        time.sleep(0.2)
    raise SupervisorUpgradeError("SUPERVISOR_STOP_TIMEOUT")


def _launch_supervisor(root: Path) -> int:
    root = Path(root).resolve()
    system = root / "_System"
    python = (
        system / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else system / ".venv" / "bin" / "python"
    )
    supervisor = system / "supervisor.py"
    if not python.is_file():
        raise SupervisorUpgradeError(f"Supervisor Python missing: {python}")
    if not supervisor.is_file():
        raise SupervisorUpgradeError(f"Supervisor source missing: {supervisor}")

    kwargs = {
        "cwd": str(root),
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
    proc = subprocess.Popen([str(python), str(supervisor), "run"], **kwargs)
    return int(proc.pid)


def _wait_for_version(
    status_file: Path,
    version: str,
    *,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            payload = json.loads(Path(status_file).read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if (
            str(payload.get("supervisor_version") or "") == str(version)
            and str(payload.get("state") or "") in {
                "starting",
                "running",
                "runtime_restarting",
                "updating",
            }
        ):
            return payload
        time.sleep(0.25)
    raise SupervisorUpgradeError(
        f"SUPERVISOR_HEALTH_TIMEOUT: expected {version}"
    )


def _replace_files(files: list[dict]) -> list[dict]:
    replaced = []
    try:
        for item in files:
            staged = Path(str(item["staged"])).resolve()
            target = Path(str(item["target"])).resolve()
            expected = str(item["sha256"])
            if not staged.is_file():
                raise SupervisorUpgradeError(f"Staged file missing: {staged}")
            actual = _sha256(staged)
            if actual != expected:
                raise SupervisorUpgradeError(
                    f"Staged hash mismatch: {staged} {actual} != {expected}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".supervisor_upgrade.tmp")
            shutil.copy2(staged, temp)
            if _sha256(temp) != expected:
                raise SupervisorUpgradeError(f"Temporary copy hash mismatch: {target}")
            temp.replace(target)
            replaced.append(dict(item))
    except Exception:
        _restore_files(reversed(replaced))
        raise
    return replaced


def _restore_files(files) -> None:
    for item in files:
        target = Path(str(item["target"])).resolve()
        backup = Path(str(item["backup"])).resolve()
        try:
            if bool(item.get("target_existed", True)):
                if backup.is_file():
                    temp = target.with_name(target.name + ".supervisor_rollback.tmp")
                    shutil.copy2(backup, temp)
                    temp.replace(target)
            else:
                target.unlink(missing_ok=True)
        except Exception:
            pass


def perform(
    request: dict,
    *,
    launch_supervisor: Callable[[Path], int] = _launch_supervisor,
    wait_for_exit: Callable[..., None] = _wait_for_supervisor_exit,
    wait_for_version: Callable[..., dict] = _wait_for_version,
) -> dict:
    if not isinstance(request, dict):
        raise ValueError("Supervisor upgrade request must be an object")

    root = Path(str(request["root"])).resolve()
    data_dir = Path(str(request["data_dir"])).resolve()
    recovery_id = str(request["recovery_id"])
    current_version = str(request["current_version"])
    target_version = str(request["target_version"])
    files = list(request.get("files") or [])
    if not files:
        raise SupervisorUpgradeError("Supervisor upgrade file list is empty")

    result_path = Path(
        str(request.get("result_path") or data_dir / f"supervisor_upgrade_result_{recovery_id}.json")
    ).resolve()
    stop_file = data_dir / "supervisor.stop"
    pid_file = data_dir / "supervisor.pid"
    status_file = data_dir / "supervisor_status.json"
    delay = max(0.0, min(float(request.get("delay_seconds") or 0.0), 10.0))
    if delay:
        time.sleep(delay)

    started_at = time.time()
    replaced: list[dict] = []
    try:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(f"supervisor upgrade {recovery_id}", encoding="utf-8")
        wait_for_exit(pid_file, timeout=float(request.get("stop_timeout_seconds") or 20.0))

        replaced = _replace_files(files)
        new_pid = launch_supervisor(root)
        status = wait_for_version(
            status_file,
            target_version,
            timeout=float(request.get("health_timeout_seconds") or 30.0),
        )
        outcome = {
            "status": "upgraded",
            "recovery_id": recovery_id,
            "current_version": current_version,
            "target_version": target_version,
            "new_supervisor_pid": new_pid,
            "supervisor_status": status,
            "rollback_performed": False,
            "started_at": started_at,
            "completed_at": time.time(),
        }
        _write_json(result_path, outcome)
        return outcome
    except Exception as exc:
        if replaced:
            # If the new Supervisor partially started, request a normal stop before rollback.
            try:
                stop_file.write_text(
                    f"supervisor rollback {recovery_id}",
                    encoding="utf-8",
                )
                wait_for_exit(
                    pid_file,
                    timeout=float(request.get("rollback_stop_timeout_seconds") or 12.0),
                )
            except Exception:
                pass
            _restore_files(reversed(replaced))
        rollback_status = None
        rollback_pid = None
        try:
            rollback_pid = launch_supervisor(root)
            rollback_status = wait_for_version(
                status_file,
                current_version,
                timeout=float(request.get("health_timeout_seconds") or 30.0),
            )
        except Exception as rollback_exc:
            outcome = {
                "status": "rollback_failed",
                "recovery_id": recovery_id,
                "current_version": current_version,
                "target_version": target_version,
                "failure": f"{type(exc).__name__}: {exc}",
                "rollback_failure": f"{type(rollback_exc).__name__}: {rollback_exc}",
                "started_at": started_at,
                "completed_at": time.time(),
            }
            _write_json(result_path, outcome)
            return outcome

        outcome = {
            "status": "rolled_back",
            "recovery_id": recovery_id,
            "current_version": current_version,
            "target_version": target_version,
            "failure": f"{type(exc).__name__}: {exc}",
            "rollback_performed": True,
            "rollback_supervisor_pid": rollback_pid,
            "rollback_status": rollback_status,
            "started_at": started_at,
            "completed_at": time.time(),
        }
        _write_json(result_path, outcome)
        return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))

        # On Windows, Runtime is later stopped by Supervisor 0.1.2 using
        # taskkill /T. DETACHED_PROCESS does not remove a directly spawned
        # child from that process tree. The first generation therefore only
        # spawns a second detached generation and exits. Because perform()
        # honors delay_seconds before asking Supervisor to stop, the first
        # generation is gone before the Runtime tree kill occurs.
        if os.name == "nt" and os.environ.get(DETACHED_WORKER_ENV) != "1":
            _spawn_detached_generation(request_path)
            return 0

        perform(request)
        return 0
    except Exception as exc:
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            result_path = Path(str(payload.get("result_path") or "")).resolve()
            if str(result_path):
                _write_json(result_path, {
                    "status": "worker_failed",
                    "recovery_id": payload.get("recovery_id"),
                    "failure": f"{type(exc).__name__}: {exc}",
                    "completed_at": time.time(),
                })
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
