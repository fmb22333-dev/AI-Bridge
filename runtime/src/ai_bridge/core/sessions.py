from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import RLock

from ai_bridge.protocol.capability import CapabilityDescriptor
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_STALE_AFTER_SECONDS = 12.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    adapter: str
    adapter_version: str
    host_version: str
    pid: int
    project_file: str
    capabilities: tuple[CapabilityDescriptor, ...] = field(default_factory=tuple)
    registered_at: str = field(default_factory=utc_now_iso)
    last_seen_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class SessionStatus:
    session_id: str
    state: str
    seconds_since_seen: float
    last_seen_at: str


class SessionRegistry:
    def __init__(self, *, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.stale_after_seconds = float(stale_after_seconds)
        self._items: dict[str, SessionInfo] = {}
        self._busy_unknown: dict[str, dict] = {}
        self._lock = RLock()

    def register(self, info: SessionInfo, *, preserve_timestamps: bool = False) -> None:
        with self._lock:
            existing = self._items.get(info.session_id)
            if existing is not None:
                immutable_match = (
                    existing.adapter == info.adapter
                    and existing.adapter_version == info.adapter_version
                    and existing.host_version == info.host_version
                    and existing.pid == info.pid
                )
                if not immutable_match:
                    raise ValueError(f"session identity conflict: {info.session_id}")
                self._items[info.session_id] = replace(
                    existing,
                    project_file=info.project_file,
                    capabilities=info.capabilities,
                    last_seen_at=info.last_seen_at if preserve_timestamps else utc_now_iso(),
                )
                return
            if preserve_timestamps:
                self._items[info.session_id] = info
            else:
                now = utc_now_iso()
                self._items[info.session_id] = replace(info, registered_at=now, last_seen_at=now)

    def heartbeat(self, session_id: str, *, project_file: str) -> SessionInfo:
        with self._lock:
            existing = self._items[session_id]
            updated = replace(existing, project_file=project_file, last_seen_at=utc_now_iso())
            self._items[session_id] = updated
            return updated

    def touch(self, session_id: str) -> SessionInfo:
        """Refresh transport liveness without changing session identity or project state."""
        with self._lock:
            existing = self._items[session_id]
            updated = replace(existing, last_seen_at=utc_now_iso())
            self._items[session_id] = updated
            return updated

    def get(self, session_id: str) -> SessionInfo:
        with self._lock:
            return self._items[session_id]

    def mark_busy_unknown(self, session_id: str, *, command_id: str, operation: str) -> dict:
        with self._lock:
            if session_id not in self._items:
                raise KeyError(session_id)
            state = {
                "session_id": session_id,
                "state": "busy_unknown",
                "source_command_id": str(command_id),
                "source_operation": str(operation),
                "marked_at": utc_now_iso(),
            }
            self._busy_unknown[session_id] = state
            return dict(state)

    def busy_unknown(self, session_id: str) -> dict | None:
        with self._lock:
            state = self._busy_unknown.get(session_id)
            return None if state is None else dict(state)

    def clear_busy_unknown(self, session_id: str, *, command_id: str | None = None) -> bool:
        with self._lock:
            state = self._busy_unknown.get(session_id)
            if state is None:
                return False
            if command_id is not None and str(state.get("source_command_id")) != str(command_id):
                return False
            self._busy_unknown.pop(session_id, None)
            return True

    def status(self, session_id: str, *, now: datetime | None = None) -> SessionStatus:
        info = self.get(session_id)
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        seconds = max(0.0, (now_utc - _parse_utc(info.last_seen_at)).total_seconds())
        state = "connected" if seconds <= self.stale_after_seconds else "disconnected"
        if state == "connected" and self.busy_unknown(session_id) is not None:
            state = "busy_unknown"
        return SessionStatus(
            session_id=session_id,
            state=state,
            seconds_since_seen=seconds,
            last_seen_at=info.last_seen_at,
        )

    def is_active(self, session_id: str) -> bool:
        return self.status(session_id).state in {"connected", "busy_unknown"}

    def list(self, *, adapter: str | None = None) -> list[SessionInfo]:
        with self._lock:
            values = list(self._items.values())
        if adapter is not None:
            values = [x for x in values if x.adapter == adapter]
        return values


class SessionRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    host_version: str = Field(min_length=1)
    pid: int
    project_file: str
    capabilities: list[CapabilityDescriptor] = Field(default_factory=list)

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            adapter=self.adapter,
            adapter_version=self.adapter_version,
            host_version=self.host_version,
            pid=self.pid,
            project_file=self.project_file,
            capabilities=tuple(self.capabilities),
        )


class SessionHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_file: str
