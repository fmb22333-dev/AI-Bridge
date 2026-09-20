from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, RLock
from typing import Callable
import time

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult


@dataclass
class PendingResult:
    command: CommandEnvelope
    event: Event
    result: ExecutionResult | None = None
    dispatched: bool = False


class AdapterCommandBus:
    def __init__(self) -> None:
        self._queues: dict[str, Queue[CommandEnvelope]] = {}
        self._pending: dict[str, PendingResult] = {}
        self._canceled: set[str] = set()
        self._timed_out: dict[str, dict] = {}
        self._timeout_handler: Callable | None = None
        self._late_completion_handler: Callable | None = None
        self._lock = RLock()

    def set_timeout_handler(self, handler) -> None:
        with self._lock:
            self._timeout_handler = handler

    def set_late_completion_handler(self, handler) -> None:
        with self._lock:
            self._late_completion_handler = handler

    def busy_source(self, session_id: str) -> dict | None:
        with self._lock:
            rows = [dict(item) for item in self._timed_out.values() if item.get("session_id") == session_id]
        if not rows:
            return None
        rows.sort(key=lambda item: float(item.get("timed_out_at") or 0.0), reverse=True)
        return rows[0]

    def ensure_session(self, session_id: str) -> None:
        with self._lock:
            self._queues.setdefault(session_id, Queue())

    def submit(self, session_id: str, command: CommandEnvelope, *, timeout: float) -> ExecutionResult | None:
        self.ensure_session(session_id)
        pending = PendingResult(command=command, event=Event())
        with self._lock:
            self._pending[command.command_id] = pending
            self._queues[session_id].put(command)
        if not pending.event.wait(timeout):
            with self._lock:
                self._pending.pop(command.command_id, None)
                state = {
                    "session_id": session_id,
                    "command_id": command.command_id,
                    "operation": command.operation,
                    "timed_out_at": time.monotonic(),
                }
                self._timed_out[command.command_id] = state
                handler = self._timeout_handler
                if callable(handler):
                    handler(session_id, command)
            return None
        with self._lock:
            done = self._pending.pop(command.command_id, None)
        return None if done is None else done.result

    def poll(self, session_id: str, *, timeout: float = 20.0) -> CommandEnvelope | None:
        self.ensure_session(session_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                command = self._queues[session_id].get(timeout=remaining)
            except Empty:
                return None
            with self._lock:
                if command.command_id in self._canceled:
                    self._canceled.discard(command.command_id)
                    if remaining <= 0:
                        return None
                    continue
                pending = self._pending.get(command.command_id)
                if pending is None:
                    if remaining <= 0:
                        return None
                    continue
                pending.dispatched = True
                return command

    def complete(self, result: ExecutionResult) -> bool:
        # Result delivery is idempotent from the Adapter sender's perspective.
        # A Runtime restart can erase the in-memory pending map while an Adapter
        # still holds the already-finished result. Treat that stale result as
        # consumed instead of returning 409 and wedging the sender forever.
        with self._lock:
            pending = self._pending.get(result.command_id)
            if pending is None:
                timed_out = self._timed_out.pop(result.command_id, None)
                handler = self._late_completion_handler
                if timed_out is not None and callable(handler):
                    handler(str(timed_out.get("session_id") or ""), result.command_id, result)
                return True
            if pending.result is not None or pending.event.is_set():
                return True
            pending.result = result
            pending.event.set()
            return True

    def pending_commands(self) -> list[tuple[CommandEnvelope, bool]]:
        with self._lock:
            return [(p.command, p.dispatched) for p in self._pending.values()]

    def cancel(self, command_id: str, result: ExecutionResult) -> bool:
        with self._lock:
            pending = self._pending.get(command_id)
            if pending is None or pending.dispatched:
                return False
            pending.result = result
            self._canceled.add(command_id)
            pending.event.set()
            return True
