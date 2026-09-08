from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, RLock
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
        self._lock = RLock()

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
        with self._lock:
            pending = self._pending.get(result.command_id)
            if pending is None:
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
