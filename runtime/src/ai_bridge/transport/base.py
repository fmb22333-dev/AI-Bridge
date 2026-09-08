from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult


@dataclass(frozen=True)
class TransportHealth:
    ok: bool
    detail: str = ""


class RemoteTransport(Protocol):
    @property
    def key(self) -> str: ...
    def fetch_commands(self) -> list[CommandEnvelope]: ...
    def publish_result(self, result: ExecutionResult) -> None: ...
    def health(self) -> TransportHealth: ...
