from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult
from .base import TransportHealth


_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TERMINAL_STATES = {"success", "failed", "denied", "conflict", "unknown", "rejected"}


@dataclass(frozen=True)
class SupabaseBusConfig:
    project_url: str
    secret_key: str
    bridge_id: str
    table: str = "ai_bridge_commands"
    timeout_seconds: float = 10.0
    batch_size: int = 50


class SupabaseBusTransport:
    """Primary realtime command transport backed by Supabase PostgREST.

    GitHub remains the durable authority and fallback command transport. Both
    transports use the same canonical command envelope and command_id. BridgeDB
    remains execution authority and provides cross-transport idempotency.
    """

    def __init__(self, config: SupabaseBusConfig, client: httpx.Client | None = None) -> None:
        project_url = str(config.project_url or "").strip().rstrip("/")
        secret_key = str(config.secret_key or "").strip()
        bridge_id = str(config.bridge_id or "").strip()
        table = str(config.table or "").strip()
        if not project_url.startswith("https://"):
            raise ValueError("Supabase project_url must use https://")
        if not secret_key:
            raise ValueError("Supabase secret_key is required")
        if secret_key.startswith("sb_publishable_"):
            raise ValueError("Supabase publishable keys cannot be used by Bridge backend transport")
        if not bridge_id:
            raise ValueError("Supabase bridge_id is required")
        if not _TABLE_RE.fullmatch(table):
            raise ValueError("Supabase table must be a simple SQL identifier")
        if not (1 <= int(config.batch_size) <= 500):
            raise ValueError("Supabase batch_size must be between 1 and 500")
        if float(config.timeout_seconds) <= 0:
            raise ValueError("Supabase timeout_seconds must be positive")

        self.config = SupabaseBusConfig(
            project_url=project_url,
            secret_key=secret_key,
            bridge_id=bridge_id,
            table=table,
            timeout_seconds=float(config.timeout_seconds),
            batch_size=int(config.batch_size),
        )
        self.client = client or httpx.Client(timeout=self.config.timeout_seconds)
        self._receipts: dict[str, list[dict]] = {}

    @property
    def key(self) -> str:
        host = self.config.project_url.removeprefix("https://").split("/", 1)[0]
        return f"supabase_bus:{host}:{self.config.bridge_id}:{self.config.table}"

    @property
    def _table_url(self) -> str:
        return f"{self.config.project_url}/rest/v1/{self.config.table}"

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "apikey": self.config.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if not self.config.secret_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.config.secret_key}"
        return headers

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        text = (response.text or "").strip().replace("\n", " ")
        return f"HTTP {response.status_code}" + (f": {text[:500]}" if text else "")

    def _get(self, *, params) -> list[dict]:
        response = self.client.get(self._table_url, headers=self.headers, params=params)
        if response.status_code >= 400:
            raise RuntimeError("Supabase GET failed: " + self._detail(response))
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Supabase GET returned a non-list payload")
        return [item for item in payload if isinstance(item, dict)]

    def _patch(self, command_id: str, body: dict, *, queued_only: bool = False) -> list[dict]:
        params = [
            ("command_id", f"eq.{command_id}"),
            ("bridge_id", f"eq.{self.config.bridge_id}"),
        ]
        if queued_only:
            params.append(("state", "eq.queued"))
        headers = dict(self.headers)
        headers["Prefer"] = "return=representation"
        response = self.client.patch(
            self._table_url,
            headers=headers,
            params=params,
            json=body,
        )
        if response.status_code >= 400:
            raise RuntimeError("Supabase PATCH failed: " + self._detail(response))
        payload = response.json() if response.content else []
        if not isinstance(payload, list):
            raise RuntimeError("Supabase PATCH returned a non-list payload")
        return [item for item in payload if isinstance(item, dict)]

    def _current_row(self, command_id: str) -> dict | None:
        rows = self._get(
            params=[
                ("select", "command_id,state,result,envelope"),
                ("command_id", f"eq.{command_id}"),
                ("bridge_id", f"eq.{self.config.bridge_id}"),
                ("limit", "1"),
            ]
        )
        return rows[0] if rows else None

    def health(self) -> TransportHealth:
        try:
            self._get(
                params=[
                    ("select", "command_id"),
                    ("bridge_id", f"eq.{self.config.bridge_id}"),
                    ("limit", "1"),
                ]
            )
            return TransportHealth(True, "Supabase Data API reachable")
        except Exception as exc:
            return TransportHealth(False, f"{type(exc).__name__}: {exc}")

    def message_state(self) -> dict:
        return {
            "mode": "supabase_fallback",  # compatibility value through 0.2.6.x
            "provider": "supabase",
            "bridge_id": self.config.bridge_id,
            "table": self.config.table,
            "parallel_with_primary": True,  # compatibility field through 0.2.6.x
            "parallel_with_fallback": True,
            "role": "primary_fast",
            "multi_channel": True,
            "multi_ai": True,
            "same_session_serial": True,
            "max_execution_lanes": 8,
        }

    def _record_receipt(self, command_id: str) -> None:
        self._receipts[command_id] = [
            {
                "receipt_id": f"supabase:{self.config.bridge_id}:{command_id}",
                "ingress_kind": "supabase",
                "routing": {"command_id": command_id},
            }
        ]

    def command_receipts(self, command_id: str) -> list[dict]:
        return [dict(item) for item in self._receipts.get(command_id, [])]

    def requires_durable_ack(self, command: CommandEnvelope) -> bool:
        return True

    def _reject_invalid_row(self, row: dict, message: str) -> None:
        command_id = str(row.get("command_id") or "").strip()
        if not command_id:
            return
        now = self._utc_now()
        self._patch(
            command_id,
            {
                "state": "rejected",
                "error": {
                    "code": "INVALID_COMMAND_ENVELOPE",
                    "message": message[:1000],
                },
                "updated_at": now,
                "finished_at": now,
            },
        )

    def fetch_commands(self) -> list[CommandEnvelope]:
        rows = self._get(
            params=[
                ("select", "command_id,envelope,state,result,created_at"),
                ("bridge_id", f"eq.{self.config.bridge_id}"),
                ("state", "in.(queued,accepted)"),
                ("order", "created_at.asc"),
                ("limit", str(self.config.batch_size)),
            ]
        )
        commands: list[CommandEnvelope] = []
        seen: dict[str, str] = {}
        for row in rows:
            row_id = str(row.get("command_id") or "").strip()
            envelope = row.get("envelope")
            if not isinstance(envelope, dict):
                self._reject_invalid_row(row, "envelope must be a JSON object")
                continue
            try:
                command = CommandEnvelope.model_validate(envelope)
            except ValidationError as exc:
                self._reject_invalid_row(row, f"ValidationError: {exc}")
                continue
            if row_id != command.command_id:
                self._reject_invalid_row(
                    row,
                    f"row command_id {row_id!r} does not match envelope command_id {command.command_id!r}",
                )
                continue
            signature = command.model_dump_json()
            prior = seen.get(command.command_id)
            if prior is not None and prior != signature:
                raise RuntimeError(f"COMMAND_IDENTITY_CONFLICT: {command.command_id}")
            seen[command.command_id] = signature
            self._record_receipt(command.command_id)
            commands.append(command)
        return commands

    def ack_command(self, command: CommandEnvelope) -> None:
        now = self._utc_now()
        rows = self._patch(
            command.command_id,
            {
                "state": "accepted",
                "accepted_at": now,
                "updated_at": now,
            },
            queued_only=True,
        )
        if rows:
            return
        current = self._current_row(command.command_id)
        if current is None:
            raise RuntimeError(f"Supabase command disappeared before ACK: {command.command_id}")
        state = str(current.get("state") or "")
        if state == "accepted" or state in _TERMINAL_STATES:
            return
        raise RuntimeError(f"Supabase command cannot be ACKed from state {state!r}")

    def publish_result_for_receipt(self, result: ExecutionResult, receipt: dict) -> None:
        routing = receipt.get("routing") if isinstance(receipt.get("routing"), dict) else {}
        command_id = str(routing.get("command_id") or result.command_id)
        if command_id != result.command_id:
            raise RuntimeError("Supabase receipt command_id does not match result command_id")
        now = self._utc_now()
        rows = self._patch(
            command_id,
            {
                "state": result.status.value,
                "result": result.model_dump(mode="json"),
                "error": (
                    result.failure.model_dump(mode="json")
                    if result.failure is not None
                    else None
                ),
                "finished_at": now,
                "updated_at": now,
            },
        )
        if not rows:
            raise RuntimeError(f"Supabase result target not found: {command_id}")

    def publish_result(self, result: ExecutionResult) -> None:
        self.publish_result_for_receipt(
            result,
            {
                "receipt_id": f"supabase:{self.config.bridge_id}:{result.command_id}",
                "ingress_kind": "supabase",
                "routing": {"command_id": result.command_id},
            },
        )
