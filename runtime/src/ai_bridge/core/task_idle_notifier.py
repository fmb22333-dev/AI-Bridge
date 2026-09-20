from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


_LOG = logging.getLogger(__name__)


def _timestamp_epoch(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    if "T" not in text:
        text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def send_windows_task_finished_notification(
    *,
    title: str = "GPT 任务结束",
    message: str = "AI Bridge 已空闲 5 分钟，最近的业务命令均已完成。",
) -> bool:
    if os.name != "nt":
        return False
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return False

    ps_title = title.replace("'", "''")
    ps_message = message.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n=New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon=[System.Drawing.SystemIcons]::Information; "
        "$n.BalloonTipIcon=[System.Windows.Forms.ToolTipIcon]::Info; "
        f"$n.BalloonTipTitle='{ps_title}'; "
        f"$n.BalloonTipText='{ps_message}'; "
        "$n.Visible=$true; "
        "$n.ShowBalloonTip(10000); "
        "Start-Sleep -Milliseconds 11000; "
        "$n.Dispose();"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return True


class TaskIdleNotifier:
    """Notify once after Bridge has had no unfinished business work for a while."""

    def __init__(
        self,
        db,
        *,
        state_path: Path,
        idle_seconds: float = 300.0,
        poll_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
        sender: Callable[..., bool] = send_windows_task_finished_notification,
    ) -> None:
        self.db = db
        self.state_path = Path(state_path)
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.poll_seconds = max(0.25, float(poll_seconds))
        self.clock = clock
        self.sender = sender
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bootstrapped = False
        self._state = self._load_state()

    def _load_state(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_state(
        self,
        key: str,
        *,
        command_id: str,
        completed_at: str,
        delivered: bool,
        reason: str,
    ) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "last_processed_key": key,
            "command_id": command_id,
            "completed_at": completed_at,
            "delivered": bool(delivered),
            "reason": reason,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.state_path)
        self._state = payload

    def check_once(self) -> bool:
        snapshot = self.db.task_idle_snapshot()
        pending_count = int(snapshot.get("pending_count") or 0)
        latest = snapshot.get("latest_terminal")
        first_check = not self._bootstrapped
        self._bootstrapped = True

        if pending_count > 0 or not isinstance(latest, dict):
            return False

        command_id = str(latest.get("command_id") or "").strip()
        completed_at = str(latest.get("updated_at") or "").strip()
        if not command_id or not completed_at:
            return False

        key = f"{command_id}:{completed_at}"
        if self._state.get("last_processed_key") == key:
            return False

        age_seconds = max(0.0, self.clock() - _timestamp_epoch(completed_at))
        if age_seconds < self.idle_seconds:
            return False

        # A newly-installed/restarted Runtime must not toast for an old command
        # that was already idle before this notifier existed.
        if first_check:
            self._save_state(
                key,
                command_id=command_id,
                completed_at=completed_at,
                delivered=False,
                reason="startup_stale_suppressed",
            )
            return False

        delivered = False
        try:
            delivered = bool(self.sender())
        except Exception:
            _LOG.exception("Windows task-finished notification failed")
        self._save_state(
            key,
            command_id=command_id,
            completed_at=completed_at,
            delivered=delivered,
            reason="idle_threshold_reached",
        )
        return delivered

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:
                _LOG.exception("Task idle notifier check failed")
            if self._stop.wait(self.poll_seconds):
                break

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="AI-Bridge-TaskIdleNotifier",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.poll_seconds + 0.5))
        self._thread = None
