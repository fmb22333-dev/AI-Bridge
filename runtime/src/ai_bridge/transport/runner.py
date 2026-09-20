from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ai_bridge.core.service import BridgeService, DuplicateCommandError
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin
from .base import RemoteTransport
from .capability_authority import search_runtime_local_capability
from .result_delivery import prepare_result_delivery


STATUS_ADAPTER = "bridge_transport"
STATUS_OPERATION = "command.status"
PING_OPERATION = "transport.ping"
TRANSPORT_ACCEPTED = "transport_accepted"


class TransportRunner:
    def __init__(self, service: BridgeService, transport: RemoteTransport, activity_observer=None) -> None:
        self.service = service
        self.transport = transport
        self.activity_observer = activity_observer
        self._capability_search_cache: dict[tuple[str, ...], tuple[str, ExecutionResult]] = {}
        self._capability_search_cache_limit = 128
        self._scheduler_lock = threading.RLock()
        self._publication_lock = threading.RLock()
        self._lane_queues: dict[str, deque] = {}
        self._lane_active: set[str] = set()
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="AI-Bridge-Lane")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _session_capability_digest(session) -> str:
        rows=[]
        for capability in getattr(session, "capabilities", ()) or ():
            payload=capability.model_dump(mode="json") if hasattr(capability, "model_dump") else (dict(capability) if isinstance(capability, dict) else {"value":str(capability)})
            rows.append(payload)
        rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",",":")))
        raw=json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _capability_normalized_arguments(command) -> dict:
        return {
            str(key): value
            for key, value in (command.arguments or {}).items()
            if str(key) not in {"detail", "force_host"}
        }

    def _capability_cache_key(self, command) -> tuple[str, ...] | None:
        if command.operation != "capability.search" or not command.session:
            return None
        try:
            session = self.service.sessions.get(command.session)
        except Exception:
            return None
        arguments = json.dumps(
            self._capability_normalized_arguments(command),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return (
            str(command.session),
            str(session.adapter),
            str(session.adapter_version),
            self._session_capability_digest(session),
            str(command.project_file or session.project_file or ""),
            arguments,
        )

    @staticmethod
    def _capability_integrity_ok(result: ExecutionResult) -> bool:
        integrity = result.result.get("integrity") if isinstance(result.result, dict) else None
        return (
            result.status == ExecutionStatus.SUCCESS
            and isinstance(integrity, dict)
            and integrity.get("ok") is True
        )

    def _remember_capability_result(self, key, command, result: ExecutionResult) -> None:
        if not self._capability_integrity_ok(result):
            return
        if len(self._capability_search_cache) >= self._capability_search_cache_limit:
            oldest = next(iter(self._capability_search_cache), None)
            if oldest is not None:
                self._capability_search_cache.pop(oldest, None)
        self._capability_search_cache[key] = (command.command_id, result.model_copy(deep=True))

    @staticmethod
    def _mark_capability_authority(result: ExecutionResult, *, mode: str, catalog_digest: str, reason: str | None = None) -> None:
        bridge = result.result.setdefault("_bridge", {})
        authority = bridge.setdefault("capability_authority", {})
        authority.update({"mode": mode, "catalog_digest": catalog_digest})
        if reason:
            authority["reason"] = reason

    def _cached_capability_result(self, command, key) -> ExecutionResult | None:
        cached = self._capability_search_cache.get(key)
        if cached is None:
            return None
        source_command_id, source_result = cached
        result = source_result.model_copy(deep=True)
        result.command_id = command.command_id
        result.evidence_id = f"ev_{command.command_id}"
        bridge = result.result.setdefault("_bridge", {})
        bridge.pop("timing", None)
        bridge.pop("execution_budget", None)
        bridge["capability_cache"] = {
            "hit": True,
            "source_command_id": source_command_id,
            "catalog_digest": key[3],
        }
        return result

    def _execute_with_capability_cache(self, command) -> ExecutionResult:
        key = self._capability_cache_key(command)
        if key is None:
            return self.service.execute(command)

        force_host = bool(command.arguments.get("force_host"))
        if force_host:
            result = self.service.execute(command)
            self._mark_capability_authority(
                result, mode="host_forced", catalog_digest=key[3], reason="force_host=true"
            )
            result.result.setdefault("_bridge", {})["capability_cache"] = {
                "hit": False, "catalog_digest": key[3], "bypassed": True
            }
            self._remember_capability_result(key, command, result)
            return result

        cached = self._cached_capability_result(command, key)
        if cached is not None:
            return cached

        try:
            session = self.service.sessions.get(command.session)
        except Exception:
            return self.service.execute(command)

        local_result, fallback_reason = search_runtime_local_capability(command, session, key[3])
        if local_result is not None:
            local_result.result.setdefault("_bridge", {})["capability_cache"] = {
                "hit": False, "catalog_digest": key[3]
            }
            self._remember_capability_result(key, command, local_result)
            return local_result

        result = self.service.execute(command)
        self._mark_capability_authority(
            result, mode="host_fallback", catalog_digest=key[3], reason=fallback_reason
        )
        result.result.setdefault("_bridge", {})["capability_cache"] = {
            "hit": False, "catalog_digest": key[3]
        }
        self._remember_capability_result(key, command, result)
        return result

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

    def _predecessor_rejection(self, command):
        getter = getattr(self.transport, "command_predecessor", None)
        if not callable(getter):
            return None
        predecessor = getter(command.command_id)
        if predecessor is None:
            return None
        predecessor_id = str(predecessor.get("command_id") or "").strip() if isinstance(predecessor, dict) else ""
        require = str(predecessor.get("require") or "success").strip().lower() if isinstance(predecessor, dict) else ""
        if not predecessor_id or predecessor_id == command.command_id or require not in {"terminal", "success"}:
            return ("INVALID_PREDECESSOR", "predecessor metadata is invalid")
        row = self.service.db.get_command(predecessor_id)
        if row is None:
            return ("PREDECESSOR_NOT_FOUND", f"predecessor command not found: {predecessor_id}")
        terminal_result = row.get("result") if isinstance(row, dict) else None
        if not isinstance(terminal_result, dict):
            return ("PREDECESSOR_NOT_TERMINAL", f"predecessor is not terminal: {predecessor_id}")
        terminal_status = str(terminal_result.get("status") or row.get("status") or "").lower()
        if require == "success" and terminal_status != "success":
            return ("PREDECESSOR_NOT_SUCCESS", f"predecessor terminal status is {terminal_status or 'unknown'}: {predecessor_id}")
        return None

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

    def _retry_pending_receipts(self, *, max_items: int = 1) -> None:
        lister = getattr(self.service.db, "list_pending_terminal_receipts", None)
        marker = getattr(self.service.db, "mark_ingress_receipt_published", None)
        publisher = getattr(self.transport, "publish_result_for_receipt", None)
        if not callable(lister) or not callable(marker) or not callable(publisher):
            return
        try:
            pending = lister(self.transport.key, limit=max(1, int(max_items)))
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

    def _parallel_enabled(self) -> bool:
        getter = getattr(self.transport, "message_state", None)
        if not callable(getter):
            return False
        try:
            state = getter()
        except Exception:
            return False
        return isinstance(state, dict) and state.get("multi_channel") is True

    @staticmethod
    def _lane_key(command) -> str:
        if command.session:
            return f"session:{command.session}"
        if command.adapter == "workspace":
            return f"adapter:workspace:{command.workspace}"
        return f"adapter:{command.adapter}"

    @staticmethod
    def _is_ping_query(command) -> bool:
        return command.adapter == STATUS_ADAPTER and command.operation == PING_OPERATION

    def _ping_query(self, command) -> ExecutionResult:
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={
                "state": "online",
                "control_plane": True,
                "transport_key": str(getattr(self.transport, "key", "unknown")),
                "server_time": self._utc_now(),
            },
        )

    def _publish_serialized(self, command, result: ExecutionResult, *, received_at: str, execute_ms: float) -> None:
        if self._closed:
            return
        with self._publication_lock:
            if self._closed:
                return
            self._publish(command, result, received_at=received_at, execute_ms=execute_ms)

    def _execute_scheduled(self, command, received_at: str) -> None:
        self._activity(
            "command_started",
            command_id=command.command_id,
            operation=command.operation,
            adapter=command.adapter,
            workspace=command.workspace,
        )
        execute_started = time.perf_counter()
        try:
            try:
                result = self._execute_with_capability_cache(command)
            except (DuplicateCommandError, sqlite3.IntegrityError):
                existing = self.service.db.get_command(command.command_id)
                if existing is None or not isinstance(existing.get("result"), dict):
                    return
                result = ExecutionResult.model_validate(existing["result"])
            self._publish_serialized(
                command,
                result,
                received_at=received_at,
                execute_ms=(time.perf_counter() - execute_started) * 1000.0,
            )
        finally:
            self._activity("command_finished", command_id=command.command_id, operation=command.operation)

    def _drain_lane(self, lane: str) -> None:
        while True:
            with self._scheduler_lock:
                queue = self._lane_queues.get(lane)
                if not queue:
                    self._lane_active.discard(lane)
                    self._lane_queues.pop(lane, None)
                    return
                command, received_at = queue.popleft()
            self._execute_scheduled(command, received_at)

    def _schedule(self, command, received_at: str) -> None:
        lane = self._lane_key(command)
        with self._scheduler_lock:
            if self._closed:
                return
            queue = self._lane_queues.setdefault(lane, deque())
            queue.append((command, received_at))
            if lane in self._lane_active:
                return
            self._lane_active.add(lane)
            self._executor.submit(self._drain_lane, lane)

    def replace_transport(self, transport: RemoteTransport):
        with self._publication_lock:
            old = self.transport
            self.transport = transport
            self._contents_index_restored = False
            return old

    def shutdown(self, *, wait: bool = False) -> None:
        with self._scheduler_lock:
            self._closed = True
            self._lane_queues.clear()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def poll_once(self) -> int:
        count = 0
        self._activity("poll_started")
        try:
            with self._publication_lock:
                self._restore_contents_index()
                commands = self.transport.fetch_commands()
                self._persist_contents_index()
            get_command = getattr(self.service.db, "get_command", None)
            terminal_replay_only = bool(commands) and callable(get_command)
            if terminal_replay_only:
                for candidate in commands:
                    existing = get_command(candidate.command_id)
                    if not isinstance(existing, dict) or not isinstance(existing.get("result"), dict):
                        terminal_replay_only = False
                        break
            if terminal_replay_only:
                with self._publication_lock:
                    self._retry_pending_receipts(max_items=1)
            for command in commands:
                receipts = self._command_receipts(command)
                self._record_receipts(command, receipts)
                if receipts:
                    if self._all_current_receipts_published(command, receipts):
                        continue
                elif self.service.db.is_published(self.transport.key, command.command_id):
                    continue

                received_at = self._utc_now()
                durable_ack = self._requires_durable_ack(command)
                row = self.service.db.get_command(command.command_id) if durable_ack else None
                if durable_ack:
                    rejection = self._predecessor_rejection(command)
                    if rejection is not None:
                        code, message = rejection
                        reject = getattr(self.transport, "nack_command", None)
                        if callable(reject):
                            with self._publication_lock:
                                reject(command, code, message)
                        count += 1
                        continue
                    accept = getattr(self.service.db, "accept_transport_command", None)
                    if callable(accept):
                        row = accept(command)
                    elif row is None:
                        self.service.db.insert_command(command, status=TRANSPORT_ACCEPTED)
                        row = self.service.db.get_command(command.command_id)
                    ack = getattr(self.transport, "ack_command", None)
                    if callable(ack):
                        with self._publication_lock:
                            ack(command)
                    terminal_result = row.get("result") if isinstance(row, dict) else None
                    if terminal_result is not None:
                        result = ExecutionResult.model_validate(terminal_result)
                        self._publish_serialized(command, result, received_at=received_at, execute_ms=0.0)
                        count += 1
                        continue
                    if not isinstance(row, dict) or str(row.get("status") or "") != TRANSPORT_ACCEPTED:
                        continue

                if self._is_status_query(command):
                    self._activity("command_started", command_id=command.command_id, operation=command.operation, adapter=command.adapter, workspace=command.workspace)
                    started = time.perf_counter()
                    try:
                        result = self._status_query(command)
                        self._publish_serialized(command, result, received_at=received_at, execute_ms=(time.perf_counter() - started) * 1000.0)
                    finally:
                        self._activity("command_finished", command_id=command.command_id, operation=command.operation)
                    count += 1
                    continue

                if self._is_ping_query(command):
                    self._activity("command_started", command_id=command.command_id, operation=command.operation, adapter=command.adapter, workspace=command.workspace)
                    started = time.perf_counter()
                    try:
                        result = self._ping_query(command)
                        self._publish_serialized(command, result, received_at=received_at, execute_ms=(time.perf_counter() - started) * 1000.0)
                    finally:
                        self._activity("command_finished", command_id=command.command_id, operation=command.operation)
                    count += 1
                    continue

                if self._parallel_enabled():
                    self._schedule(command, received_at)
                else:
                    self._execute_scheduled(command, received_at)
                count += 1
            if not commands:
                with self._publication_lock:
                    self._retry_pending_receipts(max_items=1)
            return count
        finally:
            self._activity("poll_finished", accepted=count)
