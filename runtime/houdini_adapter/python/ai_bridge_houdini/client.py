from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import __version__
from .dispatcher import dispatch
from . import local_log


CAPABILITIES = [
    {"name": "session.status", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.context", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.node", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.parm_template", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.session_module", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.batch_nodes", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.network", "version": "1.1", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "inspect.find", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "parm.read", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "parm.write", "version": "1.0", "write": True, "risk": "L1", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "code.read", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "code.write", "version": "1.0", "write": True, "risk": "L1", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "code.patch", "version": "1.0", "write": True, "risk": "L1", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.create", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.delete", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.connect", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.disconnect", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.input_state", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.batch_connect", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "frame.set", "version": "1.0", "write": True, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "cook.execute", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "long_running": True, "tested_host_versions": ["21.0"]},
    {"name": "host.errors", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "checkpoint.create", "version": "1.0", "write": True, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "rollback.execute", "version": "1.0", "write": True, "risk": "L2", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "adapter.capabilities", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "capability.search", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "geometry.query", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "diagnostic.transaction", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "rollback_on_failure": True, "long_running": True, "tested_host_versions": ["21.0"]},
    {"name": "hip.status", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "hip.save", "version": "1.0", "write": True, "risk": "L3", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "selection.get", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "selection.set", "version": "1.0", "write": True, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.state", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.set_state", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "parm.batch_read", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "parm.batch_write", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "parm.multiparm.ensure", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "node.create_configured", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "network.validate", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "network.apply", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "network.apply_transactional", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "network.ensure_plan", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "network.ensure_transactional", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "knowledge.status", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "knowledge.search", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "recipe.list", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "recipe.get", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "recipe.validate", "version": "1.0", "write": False, "risk": "L1", "rollback": False, "verification": True, "tested_host_versions": ["21.0"]},
    {"name": "recipe.apply_validation", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "rollback_after_execution": True, "tested_host_versions": ["21.0"]},
    {"name": "recipe.apply", "version": "1.0", "write": True, "risk": "L2", "rollback": True, "verification": True, "rollback_on_failure": True, "tested_host_versions": ["21.0"]},
]


def load_connection_config() -> dict:
    url = os.environ.get("AI_BRIDGE_CORE_URL")
    token = os.environ.get("AI_BRIDGE_TOKEN")
    if url and token:
        return {"url": url.rstrip("/"), "token": token}
    path = Path.home() / ".ai_bridge" / "connection.json"
    if not path.exists():
        raise RuntimeError(f"Bridge connection config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"url": str(data["url"]).rstrip("/"), "token": str(data["token"])}


HEARTBEAT_INTERVAL_SECONDS = 3.0
POLL_TIMEOUT_SECONDS = 2.0
UI_TICK_STALE_SECONDS = 4.0
UI_COMMAND_STALL_SECONDS = 3.0
LONG_HOST_OPERATION_SECONDS = 5.0
POST_LONG_HOST_RECOVERY_SECONDS = 60.0
PROFILE_COMMAND_PREFIXES = ("ctl.r_bench", "localbench.")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _profile_command(command: dict) -> bool:
    command_id = str(command.get("command_id", ""))
    return command_id.startswith(PROFILE_COMMAND_PREFIXES)


def _safe_dispatch(hou, command: dict, session_info: dict) -> dict:
    try:
        result = dispatch(hou, command, session_info)
        if isinstance(result, dict):
            return result
        raise TypeError(f"dispatcher returned {type(result).__name__}, expected dict")
    except Exception as exc:
        command_id = str(command.get("command_id") or "unknown")
        session_id = str(session_info.get("session_id") or "")
        return {
            "command_id": command_id,
            "status": "failed",
            "stages": {"EXECUTE": "FAILED"},
            "result": {},
            "failure": {
                "origin": "adapter",
                "stage": "ui_dispatch",
                "code": "ADAPTER_DISPATCH_EXCEPTION",
                "category": "adapter_guard",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
                "suggestion": "Inspect the command arguments and adapter diagnostics before retrying.",
                "context": {"operation": command.get("operation")},
            },
            "rollback_available": False,
            "last_known_state": {"host": "alive", "session": session_id},
            "evidence_id": None,
        }


class HoudiniBridgeRuntime:
    def __init__(self, hou, config: dict | None = None) -> None:
        self.hou = hou
        self.config_is_explicit = config is not None
        self.config_lock = threading.RLock()
        self.config = dict(config) if config is not None else load_connection_config()
        self.session_id = "HOU-" + uuid.uuid4().hex[:12].upper()
        self.stop_event = threading.Event()
        self.incoming: queue.Queue[dict] = queue.Queue()
        self.outgoing: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.result_worker: threading.Thread | None = None
        self.registered = False
        self.info_lock = threading.RLock()
        self.ui_state_lock = threading.RLock()
        self.last_heartbeat = 0.0
        self.last_ui_tick_monotonic = time.monotonic()
        self.last_ui_tick_error: dict | None = None
        self.ui_recovery_grace_until = 0.0
        self.pending_ui: dict[str, tuple[dict, float]] = {}
        self.abandoned_ui: set[str] = set()
        self.session_info = self._make_session_info()

    def _make_session_info(self) -> dict:
        return {
            "session_id": self.session_id,
            "adapter": "houdini",
            "adapter_version": __version__,
            "host_version": self.hou.applicationVersionString(),
            "pid": os.getpid(),
            "project_file": self.hou.hipFile.path(),
            "capabilities": CAPABILITIES,
        }

    def _connection_snapshot(self) -> dict:
        with self.config_lock:
            return dict(self.config)

    def _refresh_connection_config(self) -> bool:
        if self.config_is_explicit:
            return False
        try:
            refreshed = load_connection_config()
        except Exception:
            return False
        with self.config_lock:
            changed = refreshed != self.config
            self.config = dict(refreshed)
        if changed:
            self.registered = False
            self.last_heartbeat = 0.0
        return changed

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: float = 30.0):
        config = self._connection_snapshot()
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            config["url"] + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + config["token"],
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

    def _session_snapshot(self):
        with self.info_lock:
            return dict(self.session_info)

    def _record_tick_error(self, exc: Exception) -> None:
        with self.ui_state_lock:
            self.last_ui_tick_error = {
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "recorded_at_ms": _now_ms(),
            }

    def _mark_ui_tick(self, *, now: float | None = None) -> None:
        with self.ui_state_lock:
            self.last_ui_tick_monotonic = time.monotonic() if now is None else float(now)

    def _ui_pump_age(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else float(now)
        with self.ui_state_lock:
            return max(0.0, current - self.last_ui_tick_monotonic)

    def _ui_pump_healthy(self, *, now: float | None = None) -> bool:
        return self._ui_pump_age(now=now) <= UI_TICK_STALE_SECONDS

    def _ui_pump_failure(self, command: dict, *, age: float) -> dict:
        command_id = str(command.get("command_id") or "unknown")
        pump_age = self._ui_pump_age()
        with self.ui_state_lock:
            pending_count = len(self.pending_ui)
        return {
            "command_id": command_id,
            "status": "failed",
            "stages": {"EXECUTE": "FAILED"},
            "result": {},
            "failure": {
                "origin": "adapter",
                "stage": "ui_dispatch",
                "code": "ADAPTER_UI_PUMP_STALLED",
                "category": "adapter_liveness",
                "message": f"Houdini UI command pump has not consumed the command for {age:.3f}s",
                "retryable": False,
                "suggestion": "Do not retry unchanged. Verify Houdini UI callback liveness or restart Houdini after staging the current Adapter.",
                "context": {
                    "operation": command.get("operation"),
                    "ui_tick_age_seconds": round(pump_age, 3),
                    "pending_ui_commands": pending_count,
                },
            },
            "rollback_available": False,
            "last_known_state": {
                "host": "degraded",
                "session": self.session_id,
                "adapter_version": __version__,
                "ui_tick_age_seconds": round(pump_age, 3),
            },
            "evidence_id": None,
        }

    def _enqueue_ui_command(self, command: dict, *, now: float | None = None) -> None:
        queued_at = time.monotonic() if now is None else float(now)
        command_id = str(command.get("command_id") or "unknown")
        with self.ui_state_lock:
            self.pending_ui[command_id] = (command, queued_at)
        self.incoming.put(command)

    def _note_host_dispatch(self, *, started: float, ended: float) -> float:
        duration = max(0.0, float(ended) - float(started))
        if duration >= LONG_HOST_OPERATION_SECONDS:
            with self.ui_state_lock:
                self.ui_recovery_grace_until = max(
                    self.ui_recovery_grace_until,
                    float(ended) + POST_LONG_HOST_RECOVERY_SECONDS,
                )
        return duration

    def _claim_ui_command(self, command: dict) -> bool:
        command_id = str(command.get("command_id") or "unknown")
        with self.ui_state_lock:
            if command_id in self.abandoned_ui:
                self.abandoned_ui.discard(command_id)
                self.pending_ui.pop(command_id, None)
                return False
            self.pending_ui.pop(command_id, None)
            return True

    def _expire_stalled_ui_commands(self, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else float(now)
        expired: list[tuple[dict, float]] = []
        with self.ui_state_lock:
            recovery_grace_until = self.ui_recovery_grace_until
            for command_id, (command, queued_at) in list(self.pending_ui.items()):
                age = current - queued_at
                if age < UI_COMMAND_STALL_SECONDS:
                    continue
                if current < recovery_grace_until:
                    continue
                self.pending_ui.pop(command_id, None)
                self.abandoned_ui.add(command_id)
                expired.append((command, age))
        for command, age in expired:
            self.outgoing.put(self._ui_pump_failure(command, age=age))
        return len(expired)

    def register(self) -> None:
        self._refresh_connection_config()
        self._request("POST", "/adapter/register", self._session_snapshot())
        self.registered = True

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.result_worker = threading.Thread(target=self._result_loop, name="AI-Bridge-Houdini-Results", daemon=True)
        self.result_worker.start()
        self.worker = threading.Thread(target=self._worker_loop, name="AI-Bridge-Houdini", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()

    def tick(self) -> None:
        self._mark_ui_tick()
        try:
            self._tick_impl()
        except Exception as exc:
            self._record_tick_error(exc)

    def _tick_impl(self) -> None:
        try:
            project_file = self.hou.hipFile.path()
        except Exception as exc:
            self._record_tick_error(exc)
        else:
            with self.info_lock:
                self.session_info["project_file"] = project_file

        for _ in range(2):
            try:
                command = self.incoming.get_nowait()
            except queue.Empty:
                return
            if not self._claim_ui_command(command):
                continue

            profile = command.get("_bridge_local_timing")
            if profile is not None:
                profile["ui_tick_dequeue_ms"] = _now_ms()
                profile["host_dispatch_start_ms"] = _now_ms()

            dispatch_started = time.monotonic()
            result = _safe_dispatch(self.hou, command, self._session_snapshot())
            dispatch_ended = time.monotonic()
            self._note_host_dispatch(started=dispatch_started, ended=dispatch_ended)

            if profile is not None:
                profile["host_dispatch_end_ms"] = _now_ms()
                profile["outgoing_enqueue_ms"] = _now_ms()
                payload = result.get("result")
                if not isinstance(payload, dict):
                    payload = {}
                    result["result"] = payload
                payload["_bridge_local_timing"] = profile
            self.outgoing.put(result)

    def _result_loop(self) -> None:
        """Upload completed host results independently from command long-polling."""
        backoff = 0.05
        pending = None
        while not self.stop_event.is_set():
            try:
                if pending is None:
                    try:
                        pending = self.outgoing.get(timeout=0.25)
                    except queue.Empty:
                        continue
                local_timing = None
                if isinstance(pending, dict):
                    payload = pending.get("result")
                    if isinstance(payload, dict):
                        local_timing = payload.get("_bridge_local_timing")
                if isinstance(local_timing, dict):
                    local_timing["result_sender_dequeue_ms"] = _now_ms()
                    local_timing["result_post_start_ms"] = _now_ms()
                self._request("POST", f"/adapter/result/{self.session_id}", pending)
                pending = None
                backoff = 0.05
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                self.registered = False
                changed = self._refresh_connection_config()
                time.sleep(0.05 if changed else backoff)
                backoff = 0.05 if changed else min(backoff * 2.0, 1.0)
            except Exception:
                local_log.exception("Houdini adapter result sender error")
                self.registered = False
                changed = self._refresh_connection_config()
                time.sleep(0.05 if changed else backoff)
                backoff = 0.05 if changed else min(backoff * 2.0, 1.0)

    def _worker_loop(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                self._expire_stalled_ui_commands()

                if not self.registered:
                    self.register()

                now = time.time()
                if now - self.last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    if self._ui_pump_healthy():
                        snapshot = self._session_snapshot()
                        self._request(
                            "POST",
                            f"/adapter/heartbeat/{self.session_id}",
                            {"project_file": snapshot["project_file"]},
                        )
                        self.last_heartbeat = now

                command = self._request(
                    "GET",
                    f"/adapter/poll/{self.session_id}?timeout={POLL_TIMEOUT_SECONDS}",
                    timeout=POLL_TIMEOUT_SECONDS + 5.0,
                )
                if command:
                    if _profile_command(command):
                        command["_bridge_local_timing"] = {"adapter_poll_return_ms": _now_ms()}
                    # Do not reject from historical UI-tick age alone. Long synchronous
                    # host work can make that timestamp stale even after the command
                    # completed. Queue the command and let the existing bounded
                    # pending-ui timeout prove whether the UI pump can actually consume it.
                    self._enqueue_ui_command(command)

                self._expire_stalled_ui_commands()
                backoff = 1.0
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                self.registered = False
                changed = self._refresh_connection_config()
                time.sleep(0.1 if changed else backoff)
                backoff = 0.25 if changed else min(backoff * 2.0, 10.0)
            except Exception:
                local_log.exception("Houdini adapter worker error")
                self.registered = False
                changed = self._refresh_connection_config()
                time.sleep(0.1 if changed else backoff)
                backoff = 0.25 if changed else min(backoff * 2.0, 10.0)
