from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from ai_bridge.core.service import BridgeService, DuplicateCommandError
from ai_bridge.protocol.command import CommandEnvelope
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

    def _command_receipts(self, command) -> list[dict]:
        getter = getattr(self.transport, "command_receipts", None)
        if not callable(getter):
            return []
        try:
            receipts = getter(command.command_id)
        except Exception:
            return []
        return [dict(item) for item in receipts if isinstance(item, dict)]

    def _record_receipts(self, command, receipts: list[dict]) -> None:
        recorder = getattr(self.service.db, "record_ingress_receipt", None)
        if not callable(recorder):
            return
        for receipt in receipts:
            receipt_id = str(receipt.get("receipt_id") or "").strip()
            ingress_kind = str(receipt.get("ingress_kind") or "").strip()
            routing = receipt.get("routing") if isinstance(receipt.get("routing"), dict) else {}
            if not receipt_id or not ingress_kind:
                continue
            recorder(
                self.transport.key,
                command.command_id,
                receipt_id,
                ingress_kind,
                routing,
            )

    def _all_current_receipts_published(self, command, receipts: list[dict]) -> bool:
        if not receipts:
            return False
        lister = getattr(self.service.db, "list_ingress_receipts", None)
        if not callable(lister):
            return False
        persisted = {
            str(item.get("receipt_id") or ""): item
            for item in lister(self.transport.key, command.command_id)
        }
        receipt_ids = [str(item.get("receipt_id") or "") for item in receipts]
        return bool(receipt_ids) and all(
            receipt_id in persisted and persisted[receipt_id].get("published_at") is not None
            for receipt_id in receipt_ids
        )

    def _publish_receipts(
        self,
        command,
        result: ExecutionResult,
        receipts: list[dict],
    ) -> bool:
        publisher = getattr(self.transport, "publish_result_for_receipt", None)
        marker = getattr(self.service.db, "mark_ingress_receipt_published", None)
        lister = getattr(self.service.db, "list_ingress_receipts", None)
        if not receipts or not callable(publisher) or not callable(marker) or not callable(lister):
            return False

        persisted = {
            str(item.get("receipt_id") or ""): item
            for item in lister(self.transport.key, command.command_id)
        }
        publishable = prepare_result_delivery(self.transport, command, result)
        for receipt in receipts:
            receipt_id = str(receipt.get("receipt_id") or "").strip()
            if not receipt_id:
                continue
            prior = persisted.get(receipt_id)
            if prior is not None and prior.get("published_at") is not None:
                continue
            try:
                publisher(publishable, receipt)
                marker(self.transport.key, receipt_id)
            except Exception as exc:
                self._activity(
                    "result_publication_deferred",
                    command_id=command.command_id,
                    receipt_id=receipt_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
        return True

    def _retry_pending_receipts(self) -> None:
        lister = getattr(self.service.db, "list_pending_terminal_receipts", None)
        marker = getattr(self.service.db, "mark_ingress_receipt_published", None)
        publisher = getattr(self.transport, "publish_result_for_receipt", None)
        if not callable(lister) or not callable(marker) or not callable(publisher):
            return
        try:
            pending = lister(self.transport.key, limit=100)
        except Exception:
            return
        for item in pending:
            try:
                command = CommandEnvelope.model_validate(item["request"])
                result = ExecutionResult.model_validate(item["result"])
                receipt = {
                    "receipt_id": item["receipt_id"],
                    "ingress_kind": item["ingress_kind"],
                    "routing": item.get("routing") or {},
                }
                publishable = prepare_result_delivery(self.transport, command, result)
                publisher(publishable, receipt)
                marker(self.transport.key, str(item["receipt_id"]))
            except Exception as exc:
                self._activity(
                    "result_publication_retry_deferred",
                    command_id=item.get("command_id"),
                    receipt_id=item.get("receipt_id"),
                    error=f"{type(exc).__name__}: {exc}",
                )

    def _publish(self, command, result: ExecutionResult, *, received_at: str, execute_ms: float) -> None:
        bridge_meta = result.result.setdefault("_bridge", {})
        timing = bridge_meta.setdefault("timing", {})
        timing.setdefault("bridge_received_at", received_at)
        timing.setdefault("execute_ms", round(execute_ms, 3))
        timing["result_publish_started_at"] = self._utc_now()
        save_result = getattr(self.service.db, "save_result", None)
        if callable(save_result):
            save_result(result)

        receipts = self._command_receipts(command)
        if receipts and self._publish_receipts(command, result, receipts):
            return

        publishable = prepare_result_delivery(self.transport, command, result)
        self.transport.publish_result(publishable)
        self.service.db.mark_published(self.transport.key, command.command_id)

    def _restore_contents_index(self) -> None:
        if getattr(self, "_contents_index_restored", False):
            return
        restorer = getattr(self.transport, "restore_contents_command_index", None)
        getter = getattr(self.service.db, "get_transport_contents_index", None)
        if not callable(restorer) or not callable(getter):
            self._contents_index_restored = True
            return
        restorer(getter(self.transport.key))
        self._contents_index_restored = True

    def _persist_contents_index(self) -> None:
        snapshotter = getattr(self.transport, "contents_command_index_snapshot", None)
        setter = getattr(self.service.db, "set_transport_contents_index", None)
        if not callable(snapshotter) or not callable(setter):
            return
        snapshot = snapshotter()
        if snapshot is not None:
            setter(self.transport.key, snapshot)

    def poll_once(self) -> int:
        count = 0
        self._activity("poll_started")
        try:
            self._retry_pending_receipts()
            self._restore_contents_index()
            commands = self.transport.fetch_commands()
            self._persist_contents_index()
            for command in commands:
                receipts = self._command_receipts(command)
                self._record_receipts(command, receipts)
                if receipts:
                    if self._all_current_receipts_published(command, receipts):
                        continue
                elif self.service.db.is_published(self.transport.key, command.command_id):
                    continue

                self._activity("command_started", command_id=command.command_id, operation=command.operation, adapter=command.adapter, workspace=command.workspace)
                try:
                    received_at = self._utc_now()
                    execute_started = time.perf_counter()
                    durable_ack = self._requires_durable_ack(command)
                    row = self.service.db.get_command(command.command_id) if durable_ack else None
                    if durable_ack:
                        accept = getattr(self.service.db, "accept_transport_command", None)
                        if callable(accept):
                            row = accept(command)
                        elif row is None:
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
                        except (DuplicateCommandError, sqlite3.IntegrityError):
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