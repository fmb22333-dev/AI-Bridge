from __future__ import annotations

import time
from datetime import datetime, timezone

from ai_bridge.core.service import BridgeService, DuplicateCommandError
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin
from .base import RemoteTransport
from .result_delivery import prepare_result_delivery


STATUS_ADAPTER = "bridge_transport"
STATUS_OPERATION = "command.status"
TRANSPORT_ACCEPTED = "transport_accepted"


class TransportRunner:
    def __init__(self, service: BridgeService, transport: RemoteTransport, activity_observer=None) -> None:
        self.service = service
        self.transport = transport
        self.activity_observer = activity_observer

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _activity(self, event: str, **payload) -> None:
        if self.activity_observer is None:
            return
        data = {"at": self._utc_now(), **payload}
        try:
            self.activity_observer(event, data)
        except Exception:
            pass

    @staticmethod
    def _is_status_query(command) -> bool:
        return command.adapter == STATUS_ADAPTER and command.operation == STATUS_OPERATION

    def _status_query(self, command) -> ExecutionResult:
        target = str(command.arguments.get("command_id") or "").strip()
        if not target:
            return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.FAILED, failure=FailureInfo(origin=FailureOrigin.CORE, code="TARGET_COMMAND_ID_REQUIRED", message="arguments.command_id is required"))
        row = self.service.db.get_command(target)
        if row is None:
            return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={"target_command_id": target, "state": "not_found", "terminal": False, "found": False})
        terminal_result = row.get("result")
        payload = {
            "target_command_id": target, "state": str(row.get("status") or "unknown"),
            "terminal": terminal_result is not None, "found": True,
            "workspace": row.get("workspace_id"), "adapter": row.get("adapter"),
            "operation": row.get("operation"), "evidence_id": row.get("evidence_id"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        }
        if bool(command.arguments.get("include_result")) and terminal_result is not None:
            payload["terminal_result"] = terminal_result
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result=payload)

    def _requires_durable_ack(self, command) -> bool:
        checker = getattr(self.transport, "requires_durable_ack", None)
        if callable(checker):
            try:
                return bool(checker(command))
            except Exception:
                return False
        return callable(getattr(self.transport, "ack_command", None))

    def _publish(self, command, result: ExecutionResult, *, received_at: str, execute_ms: float) -> None:
        bridge_meta = result.result.setdefault("_bridge", {})
        timing = bridge_meta.setdefault("timing", {})
        timing.setdefault("bridge_received_at", received_at)
        timing.setdefault("execute_ms", round(execute_ms, 3))
        timing["result_publish_started_at"] = self._utc_now()
        save_result = getattr(self.service.db, "save_result", None)
        if callable(save_result):
            save_result(result)
        publishable = prepare_result_delivery(self.transport, command, result)
        self.transport.publish_result(publishable)
        self.service.db.mark_published(self.transport.key, command.command_id)

    def poll_once(self) -> int:
        count = 0
        self._activity("poll_started")
        try:
            commands = self.transport.fetch_commands()
            for command in commands:
                if self.service.db.is_published(self.transport.key, command.command_id):
                    continue
                self._activity("command_started", command_id=command.command_id, operation=command.operation, adapter=command.adapter, workspace=command.workspace)
                try:
                    received_at = self._utc_now()
                    execute_started = time.perf_counter()
                    durable_ack = self._requires_durable_ack(command)
                    row = self.service.db.get_command(command.command_id) if durable_ack else None
                    if durable_ack:
                        if row is None:
                            self.service.db.insert_command(command, status=TRANSPORT_ACCEPTED)
                            row = self.service.db.get_command(command.command_id)
                        ack = getattr(self.transport, "ack_command", None)
                        if callable(ack):
                            ack(command)
                        terminal_result = row.get("result") if isinstance(row, dict) else None
                        if terminal_result is not None:
                            result = ExecutionResult.model_validate(terminal_result)
                            self._publish(command, result, received_at=received_at, execute_ms=(time.perf_counter() - execute_started) * 1000.0)
                            count += 1
                            continue
                        if not isinstance(row, dict) or str(row.get("status") or "") != TRANSPORT_ACCEPTED:
                            continue
                    if self._is_status_query(command):
                        result = self._status_query(command)
                    else:
                        try:
                            result = self.service.execute(command)
                        except DuplicateCommandError:
                            existing = self.service.db.get_command(command.command_id)
                            if existing is None or not isinstance(existing.get("result"), dict):
                                continue
                            result = ExecutionResult.model_validate(existing["result"])
                    self._publish(command, result, received_at=received_at, execute_ms=(time.perf_counter() - execute_started) * 1000.0)
                    count += 1
                finally:
                    self._activity("command_finished", command_id=command.command_id, operation=command.operation)
            return count
        finally:
            self._activity("poll_finished", accepted=count)
