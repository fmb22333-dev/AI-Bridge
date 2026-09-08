from __future__ import annotations

from typing import Protocol
import os
import ntpath
import time
import uuid

from ai_bridge.adapters.registry import AdapterDescriptor, AdapterRegistry
from ai_bridge.core.adapter_bus import AdapterCommandBus
from ai_bridge.core.emergency_stop import EmergencyStop
from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.host_process import HostProcessController
from ai_bridge.core.plugin_manager import HostPluginManager
from ai_bridge.core.remote_adapter import QueueAdapterExecutor
from ai_bridge.core.sessions import SessionRegistration, SessionRegistry
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.checkpoints import CheckpointStore
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.persistence.evidence import EvidenceStore
from ai_bridge.persistence.snapshots import SnapshotStore
from ai_bridge.persistence.recovery import RecoveryStore
from ai_bridge.protocol.command import CommandEnvelope, ExecutionOptions, RiskLevel
from ai_bridge.protocol.result import (
    ExecutionResult,
    ExecutionStatus,
    FailureInfo,
    FailureOrigin,
)


class AdapterExecutor(Protocol):
    def execute(self, command: CommandEnvelope) -> ExecutionResult: ...


class DuplicateCommandError(RuntimeError):
    pass


class BridgeService:
    def __init__(
        self,
        *,
        db: BridgeDB,
        workspaces: WorkspaceRegistry,
        execution_policy: ExecutionPolicyStore | None = None,
        host_process: HostProcessController | None = None,
        plugin_manager: HostPluginManager | None = None,
    ) -> None:
        self.db = db
        self.workspaces = workspaces
        self.execution_policy = execution_policy or ExecutionPolicyStore()
        self.host_process = host_process or HostProcessController()
        self.plugins = plugin_manager or HostPluginManager()
        self.adapter_registry = AdapterRegistry()
        self.sessions = SessionRegistry()
        self.adapter_bus = AdapterCommandBus()
        self.emergency_stop = EmergencyStop()
        self._executors: dict[str, AdapterExecutor] = {}
        state_root = self.db.path.parent / "state"
        self.evidence = EvidenceStore(state_root / "evidence")
        self.snapshots = SnapshotStore(state_root / "snapshots")
        self.checkpoints = CheckpointStore(state_root / "checkpoints")
        self.recoveries = RecoveryStore(state_root / "recoveries")

    def register_executor(self, adapter: str, executor: AdapterExecutor) -> None:
        self._executors[adapter] = executor

    def active_sessions(self, *, adapter: str | None = None):
        return [
            session
            for session in self.sessions.list(adapter=adapter)
            if self.sessions.is_active(session.session_id)
        ]

    def stop_writes(self) -> int:
        self.emergency_stop.stop_writes()
        canceled = 0
        for command, dispatched in self.adapter_bus.pending_commands():
            if dispatched:
                continue
            cap, _ = self._capability(command)
            if cap is None or not cap.write:
                continue
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.CORE, code="EMERGENCY_STOP"),
                last_known_state={"dispatched": False},
            )
            if self.adapter_bus.cancel(command.command_id, result):
                canceled += 1
        return canceled

    def resume_writes(self) -> None:
        self.emergency_stop.resume_writes()

    def register_adapter_session(self, registration: SessionRegistration) -> None:
        info = registration.to_info()
        self.sessions.register(info)
        self.adapter_bus.ensure_session(info.session_id)
        self.adapter_registry.register(
            AdapterDescriptor(
                adapter=info.adapter,
                adapter_version=info.adapter_version,
                endpoint="queue://" + info.session_id,
                capabilities=list(info.capabilities),
            ),
            replace=True,
        )
        if info.adapter not in self._executors:
            self._executors[info.adapter] = QueueAdapterExecutor(self.adapter_bus)

    def _capability(self, command: CommandEnvelope):
        if command.session:
            try:
                session = self.sessions.get(command.session)
            except KeyError:
                return None, "SESSION_NOT_FOUND"
            if session.adapter != command.adapter:
                return None, "SESSION_ADAPTER_MISMATCH"
            if not self.sessions.is_active(command.session):
                return None, "SESSION_DISCONNECTED"
            for cap in session.capabilities:
                if cap.name == command.operation:
                    return cap, None
            return None, "CAPABILITY_NOT_SUPPORTED"

        try:
            descriptor = self.adapter_registry.get(command.adapter)
        except KeyError:
            return None, "CAPABILITY_NOT_SUPPORTED"
        for cap in descriptor.capabilities:
            if cap.name == command.operation:
                return cap, None
        return None, "CAPABILITY_NOT_SUPPORTED"

    def _finalize(self, command: CommandEnvelope, result: ExecutionResult, *, checkpoint_id: str | None = None, write: bool = False) -> ExecutionResult:
        bridge_meta = result.result.setdefault("_bridge", {})
        budget = self.execution_policy.resolve(command)
        if budget is not None:
            bridge_meta["execution_budget"] = budget.as_dict()
        if checkpoint_id:
            bridge_meta["checkpoint_id"] = checkpoint_id
        if write:
            rollback_supported = checkpoint_id is not None or command.operation in {"code.write", "code.patch", "parm.write"}
            if rollback_supported:
                snap = self.snapshots.capture(command, result, checkpoint_id=checkpoint_id)
                result.rollback_available = snap is not None
        evidence_id = "ev_" + command.command_id.replace("/", "_").replace(":", "_")
        result.evidence_id = evidence_id
        self.evidence.write(command, result)
        self.db.save_result(result)
        return result

    def _checkpoint_before(
        self,
        command: CommandEnvelope,
        executor: AdapterExecutor,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[str | None, ExecutionResult | None]:
        if not command.session:
            return None, ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.ADAPTER, code="SESSION_REQUIRED_FOR_CHECKPOINT"),
            )
        session = self.sessions.get(command.session)
        if not any(c.name == "checkpoint.create" for c in session.capabilities):
            return None, ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.ADAPTER, code="CHECKPOINT_CAPABILITY_REQUIRED"),
            )
        checkpoint_id = "cp_" + command.command_id.replace(":", "_") + "_" + uuid.uuid4().hex[:8]
        internal = CommandEnvelope(
            command_id=command.command_id + ":checkpoint:" + uuid.uuid4().hex[:6],
            workspace=command.workspace,
            adapter=command.adapter,
            session=command.session,
            project_file=command.project_file,
            operation="checkpoint.create",
            arguments={"checkpoint_id": checkpoint_id, "source_command_id": command.command_id},
            risk=RiskLevel.L1,
        )
        if isinstance(executor, QueueAdapterExecutor) and timeout_seconds is not None:
            cp_result = executor.execute(internal, timeout=timeout_seconds)
        else:
            cp_result = executor.execute(internal)
        cp_evidence = "ev_" + internal.command_id.replace(":", "_")
        cp_result.evidence_id = cp_evidence
        self.evidence.write(internal, cp_result)
        if cp_result.status != ExecutionStatus.SUCCESS or cp_result.result.get("verified") is False:
            return None, ExecutionResult(
                command_id=command.command_id,
                status=cp_result.status if cp_result.status != ExecutionStatus.SUCCESS else ExecutionStatus.FAILED,
                failure=cp_result.failure or FailureInfo(origin=FailureOrigin.ADAPTER, stage="checkpoint", code="CHECKPOINT_FAILED"),
                last_known_state=cp_result.last_known_state,
                evidence_id=cp_evidence,
            )
        metadata = dict(cp_result.result)
        metadata.update({
            "checkpoint_id": checkpoint_id,
            "source_command_id": command.command_id,
            "workspace": command.workspace,
            "adapter": command.adapter,
            "session": command.session,
        })
        try:
            host_executable = self.host_process.executable_path(session.pid)
        except Exception:
            host_executable = None
        if host_executable:
            metadata["host_executable"] = host_executable
        self.checkpoints.write(checkpoint_id, metadata)
        return checkpoint_id, None

    @staticmethod
    def _same_project(left: str | None, right: str | None) -> bool:
        if not left or not right:
            return False
        left_text = str(left).strip()
        right_text = str(right).strip()
        windows_style = (
            (len(left_text) >= 2 and left_text[1] == ":")
            or (len(right_text) >= 2 and right_text[1] == ":")
            or "\\" in left_text
            or "\\" in right_text
        )
        path_module = ntpath if windows_style else os.path
        try:
            return path_module.normcase(path_module.abspath(left_text)) == path_module.normcase(
                path_module.abspath(right_text)
            )
        except Exception:
            left_fallback = left_text.replace("\\", "/")
            right_fallback = right_text.replace("\\", "/")
            if windows_style:
                left_fallback = left_fallback.casefold()
                right_fallback = right_fallback.casefold()
            return left_fallback == right_fallback

    def _wait_for_replacement_session(
        self,
        *,
        adapter: str,
        project_file: str,
        old_session_id: str,
        old_pid: int,
        timeout_seconds: float = 120.0,
        expected_adapter_version: str | None = None,
    ):
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            for session in self.sessions.list(adapter=adapter):
                if session.session_id == old_session_id or session.pid == old_pid:
                    continue
                if not self._same_project(session.project_file, project_file):
                    continue
                if expected_adapter_version and session.adapter_version != expected_adapter_version:
                    continue
                if self.sessions.is_active(session.session_id):
                    return session
            time.sleep(0.25)
        return None

    def _latest_verified_checkpoint(
        self,
        *,
        adapter: str,
        workspace: str,
        project_file: str,
        limit: int = 100,
    ) -> dict | None:
        for item in self.checkpoints.recent(limit=limit):
            if item.get("verified") is False:
                continue
            if item.get("adapter") and str(item.get("adapter")) != str(adapter):
                continue
            if item.get("workspace") and str(item.get("workspace")) != str(workspace):
                continue
            original_hip = str(item.get("original_hip") or "").strip()
            if original_hip and not self._same_project(original_hip, project_file):
                continue
            checkpoint_path = str(item.get("checkpoint_path") or "").strip()
            if not checkpoint_path or not os.path.exists(checkpoint_path):
                continue
            return dict(item)
        return None

    def _latest_known_host_executable(
        self,
        *,
        adapter: str,
        workspace: str,
        project_file: str,
        checkpoint: dict | None = None,
        limit: int = 100,
    ) -> tuple[str | None, str | None]:
        checkpoint_executable = str((checkpoint or {}).get("host_executable") or "").strip()
        if checkpoint_executable and os.path.isfile(checkpoint_executable):
            return checkpoint_executable, "checkpoint"

        for item in self.recoveries.recent(limit=limit):
            item_host = str(item.get("host_id") or item.get("adapter") or "").strip()
            if item_host and item_host != adapter:
                continue
            item_workspace = str(item.get("workspace") or "").strip()
            if item_workspace and item_workspace != workspace:
                continue
            item_project = str(
                item.get("project_file") or item.get("original_hip") or ""
            ).strip()
            if item_project and not self._same_project(item_project, project_file):
                continue
            executable = str(item.get("host_executable") or "").strip()
            if executable and os.path.isfile(executable):
                return executable, "recovery_history"
        return None, None

    def force_recover_host(
        self,
        *,
        host_id: str,
        workspace: str,
        session_id: str | None = None,
        pid: int | None = None,
        project_file: str | None = None,
        checkpoint_id: str | None = None,
        allow_saved_project_fallback: bool = False,
        session_wait_seconds: float = 120.0,
        restore_timeout_seconds: float = 60.0,
        source_command_id: str | None = None,
        source_operation: str | None = None,
    ) -> dict:
        """L3 control-plane recovery that does not depend on the old Adapter queue.

        A live/stale SessionRegistry entry is preferred when available. If Runtime
        replacement or Adapter heartbeat loss removed that authority, an explicit
        PID + project_file pair may target the host directly. The old host is
        terminated through OS process control. Recovery prefers a verified
        checkpoint already present on disk; restarting the last saved project
        without a checkpoint requires explicit opt-in.
        """
        host_id = str(host_id or "").strip().lower()
        workspace = str(workspace or "").strip()
        requested_session_id = str(session_id or "").strip()
        if not host_id:
            raise ValueError("host_id is required")
        if not workspace:
            raise ValueError("workspace is required")
        self.workspaces.get(workspace)

        old_session = None
        if requested_session_id:
            try:
                old_session = self.sessions.get(requested_session_id)
            except KeyError:
                old_session = None

        if old_session is not None:
            if old_session.adapter != host_id:
                raise ValueError(
                    f"session adapter mismatch: expected {host_id}, got {old_session.adapter}"
                )
            if pid is not None and int(pid) != int(old_session.pid):
                raise RuntimeError("TARGET_PID_MISMATCH")
            if project_file and not self._same_project(project_file, old_session.project_file):
                raise RuntimeError("TARGET_PROJECT_MISMATCH")
            target_pid = int(old_session.pid)
            target_project = str(old_session.project_file)
            target_source = "session_registry"
            old_session_id = old_session.session_id
        else:
            target_pid = int(pid or 0)
            target_project = str(project_file or "").strip()
            if target_pid <= 0 or not target_project:
                raise ValueError(
                    "session_id must resolve, or explicit pid + project_file are required"
                )
            target_source = "explicit_process"
            old_session_id = requested_session_id

        recovery_id = "force_recovery_" + uuid.uuid4().hex[:12]
        report = {
            "recovery_id": recovery_id,
            "kind": "force_host_recovery",
            "privilege_level": "L3_CORE_OS",
            "control_plane": "bridge_core",
            "status": "not_started",
            "host_id": host_id,
            "workspace": workspace,
            "target_source": target_source,
            "old_session_id": old_session_id or None,
            "old_pid": target_pid,
            "project_file": target_project,
            "source_command_id": source_command_id,
            "source_operation": source_operation,
            "allow_saved_project_fallback": bool(allow_saved_project_fallback),
        }

        checkpoint = None
        checkpoint_source = None
        if checkpoint_id:
            checkpoint = self.checkpoints.read(str(checkpoint_id))
            checkpoint_source = "explicit"
            if checkpoint.get("verified") is False:
                raise RuntimeError("CHECKPOINT_NOT_VERIFIED")
            cp_adapter = str(checkpoint.get("adapter") or "").strip()
            if cp_adapter and cp_adapter != host_id:
                raise RuntimeError("CHECKPOINT_ADAPTER_MISMATCH")
            cp_workspace = str(checkpoint.get("workspace") or "").strip()
            if cp_workspace and cp_workspace != workspace:
                raise RuntimeError("CHECKPOINT_WORKSPACE_MISMATCH")
            cp_original = str(checkpoint.get("original_hip") or "").strip()
            if cp_original and not self._same_project(cp_original, target_project):
                raise RuntimeError("CHECKPOINT_PROJECT_MISMATCH")
        else:
            checkpoint = self._latest_verified_checkpoint(
                adapter=host_id,
                workspace=workspace,
                project_file=target_project,
            )
            if checkpoint is not None:
                checkpoint_source = "latest_verified"

        if checkpoint is not None:
            checkpoint_id = str(checkpoint.get("checkpoint_id") or checkpoint_id or "")
            checkpoint_path = str(checkpoint.get("checkpoint_path") or "").strip()
            original_hip = str(checkpoint.get("original_hip") or target_project).strip()
            if not checkpoint_path or not os.path.exists(checkpoint_path):
                report.update({
                    "status": "skipped",
                    "reason": "RECOVERY_CHECKPOINT_UNAVAILABLE",
                })
                self.recoveries.write(recovery_id, report)
                return report
            launch_project = checkpoint_path
            report.update({
                "checkpoint_id": checkpoint_id or None,
                "checkpoint_source": checkpoint_source,
                "checkpoint_path": checkpoint_path,
                "original_hip": original_hip,
            })
        else:
            if not allow_saved_project_fallback:
                report.update({
                    "status": "skipped",
                    "reason": "RECOVERY_CHECKPOINT_UNAVAILABLE",
                    "message": (
                        "No verified checkpoint matched this project. "
                        "Set allow_saved_project_fallback=true only to accept loss of unsaved host state."
                    ),
                })
                self.recoveries.write(recovery_id, report)
                return report
            launch_project = target_project
            original_hip = target_project
            report.update({
                "checkpoint_source": "none",
                "fallback": "last_saved_project",
                "loss_scope": "unsaved_host_state_may_be_lost",
            })

        try:
            process_running = self.host_process.is_running(target_pid)
            executable_source = "pid"
            if process_running:
                executable = self.host_process.executable_path(target_pid)
                report["status"] = "terminating"
                termination = self.host_process.terminate(target_pid)
                if not termination.get("terminated"):
                    raise RuntimeError("host process termination was not verified")
            else:
                executable, executable_source = self._latest_known_host_executable(
                    adapter=host_id,
                    workspace=workspace,
                    project_file=target_project,
                    checkpoint=checkpoint,
                )
                if not executable:
                    raise RuntimeError("HOST_EXECUTABLE_UNAVAILABLE_AFTER_EXIT")
                termination = {
                    "pid": target_pid,
                    "terminated": True,
                    "already_stopped": True,
                    "elapsed_ms": 0.0,
                }

            report["host_executable"] = executable
            report["host_executable_source"] = executable_source
            report["termination"] = termination
            report["status"] = "restarting"
            launch = self.host_process.launch(executable, launch_project)
            report["launch"] = launch
            replacement = self._wait_for_replacement_session(
                adapter=host_id,
                project_file=launch_project,
                old_session_id=old_session_id,
                old_pid=target_pid,
                timeout_seconds=session_wait_seconds,
            )
            if replacement is None:
                raise RuntimeError("replacement host session did not register before recovery timeout")

            report["new_session_id"] = replacement.session_id
            report["new_pid"] = replacement.pid

            if checkpoint is None:
                report["status"] = "recovered_saved_project"
                report["audit"] = {
                    "authority": "explicit_L3_saved_project_fallback",
                    "adapter_queue_required_for_restart": False,
                }
                self.recoveries.write(recovery_id, report)
                return report

            report["status"] = "restoring_project_identity"
            rollback_command = CommandEnvelope(
                command_id="force_recover_restore_" + uuid.uuid4().hex[:16],
                workspace=workspace,
                adapter=host_id,
                session=replacement.session_id,
                project_file=replacement.project_file,
                operation="rollback.execute",
                arguments={
                    "checkpoint_path": launch_project,
                    "original_hip": original_hip,
                    "recovery_source_command_id": source_command_id,
                },
                execution=ExecutionOptions(
                    verify=True,
                    checkpoint="none",
                    dry_run=False,
                    budget_seconds=max(1.0, float(restore_timeout_seconds)),
                    auto_recover=False,
                ),
                risk=RiskLevel.L2,
            )
            executor = self._executors.get(host_id)
            if executor is None:
                raise RuntimeError("ADAPTER_EXECUTOR_NOT_CONNECTED_AFTER_RESTART")
            if isinstance(executor, QueueAdapterExecutor):
                rollback_result = executor.execute(
                    rollback_command,
                    timeout=max(1.0, float(restore_timeout_seconds)),
                )
            else:
                rollback_result = executor.execute(rollback_command)

            report["rollback_command_id"] = rollback_command.command_id
            report["rollback_status"] = rollback_result.status.value
            report["rollback_verified"] = bool(
                rollback_result.status == ExecutionStatus.SUCCESS
                and rollback_result.result.get("verified") is not False
            )
            if not report["rollback_verified"]:
                report["status"] = "restarted_checkpoint_safe"
                report["reason"] = "PROJECT_IDENTITY_RESTORE_NOT_VERIFIED"
                if rollback_result.failure is not None:
                    report["rollback_failure"] = rollback_result.failure.model_dump(mode="json")
            else:
                report["status"] = "recovered"
                report["audit"] = {
                    "authority": "L3_CORE_OS",
                    "recovery_boundary": checkpoint_id,
                    "checkpoint_source": checkpoint_source,
                    "adapter_queue_required_for_restart": False,
                    "adapter_queue_used_only_after_fresh_host_start": True,
                }
        except Exception as exc:
            report.update({
                "status": "failed",
                "reason": type(exc).__name__,
                "message": str(exc),
            })

        self.recoveries.write(recovery_id, report)
        return report

    def _recover_budget_exceeded(
        self,
        command: CommandEnvelope,
        *,
        checkpoint_id: str | None,
        old_session,
    ) -> dict:
        recovery_id = "recovery_" + uuid.uuid4().hex[:12]
        report = {
            "recovery_id": recovery_id,
            "source_command_id": command.command_id,
            "source_operation": command.operation,
            "status": "not_started",
            "checkpoint_id": checkpoint_id,
            "old_session_id": old_session.session_id,
            "old_pid": old_session.pid,
            "project_file": old_session.project_file,
            "audit": {
                "basis": "checkpoint_before_source_command",
                "preserved": "state represented by the verified pre-command HIP checkpoint",
                "reverted_or_unconfirmed_commands": [{
                    "command_id": command.command_id,
                    "operation": command.operation,
                    "risk": command.risk.value,
                    "arguments": command.arguments,
                }],
            },
        }

        if not checkpoint_id:
            report.update({
                "status": "skipped",
                "reason": "RECOVERY_CHECKPOINT_UNAVAILABLE",
            })
            self.recoveries.write(recovery_id, report)
            return report

        try:
            checkpoint = self.checkpoints.read(checkpoint_id)
            checkpoint_path = str(checkpoint["checkpoint_path"])
            original_hip = str(checkpoint["original_hip"])
            if not os.path.exists(checkpoint_path):
                raise FileNotFoundError(checkpoint_path)

            executable = self.host_process.executable_path(old_session.pid)
            report["checkpoint_path"] = checkpoint_path
            report["original_hip"] = original_hip
            report["host_executable"] = executable
            report["status"] = "terminating"

            termination = self.host_process.terminate(old_session.pid)
            report["termination"] = termination
            if not termination.get("terminated"):
                raise RuntimeError("host process termination was not verified")

            report["status"] = "restarting"
            launch = self.host_process.launch(executable, checkpoint_path)
            report["launch"] = launch

            replacement = self._wait_for_replacement_session(
                adapter=command.adapter,
                project_file=checkpoint_path,
                old_session_id=old_session.session_id,
                old_pid=old_session.pid,
            )
            if replacement is None:
                raise RuntimeError("replacement host session did not register before recovery timeout")

            report["new_session_id"] = replacement.session_id
            report["new_pid"] = replacement.pid
            report["status"] = "restoring"

            rollback_command = CommandEnvelope(
                command_id="auto_recover_" + uuid.uuid4().hex[:16],
                workspace=command.workspace,
                adapter=command.adapter,
                session=replacement.session_id,
                project_file=replacement.project_file,
                operation="rollback.execute",
                arguments={
                    "checkpoint_path": checkpoint_path,
                    "original_hip": original_hip,
                    "recovery_source_command_id": command.command_id,
                },
                execution=ExecutionOptions(
                    verify=True,
                    checkpoint="auto",
                    dry_run=False,
                    budget_seconds=60.0,
                    auto_recover=False,
                ),
                risk=RiskLevel.L2,
            )
            rollback_result = self.execute(rollback_command)
            report["rollback_command_id"] = rollback_command.command_id
            report["rollback_status"] = rollback_result.status.value
            report["rollback_verified"] = bool(
                rollback_result.status == ExecutionStatus.SUCCESS
                and rollback_result.result.get("verified") is not False
            )
            if not report["rollback_verified"]:
                report["status"] = "failed"
                report["reason"] = "ROLLBACK_NOT_VERIFIED"
            else:
                report["status"] = "recovered"
                report["audit"]["confidence"] = "high"
                report["audit"]["recovery_boundary"] = checkpoint_id
        except Exception as exc:
            report["status"] = "failed"
            report["reason"] = type(exc).__name__
            report["message"] = str(exc)

        self.recoveries.write(recovery_id, report)
        return report

    def restart_host_for_plugin_update(
        self,
        *,
        host_id: str,
        session_id: str,
        workspace: str,
        close_wait_seconds: float = 8.0,
        session_wait_seconds: float = 120.0,
        force_restart: bool = False,
    ) -> dict:
        host_id = str(host_id or "").strip().lower()
        session = self.sessions.get(session_id)
        if session.adapter != host_id:
            raise ValueError(
                f"session adapter mismatch: expected {host_id}, got {session.adapter}"
            )
        if not self.sessions.is_active(session_id):
            raise RuntimeError("SESSION_DISCONNECTED")
        self.workspaces.get(workspace)

        install_result = self.plugins.install(host_id, host_running=True)
        plugin_status = next(
            item
            for item in self.plugins.status(
                live_sessions=self.active_sessions()
            )["hosts"]
            if item["id"] == host_id
        )
        expected_version = plugin_status.get("bundled_version")
        if not expected_version:
            raise RuntimeError("BUNDLED_PLUGIN_VERSION_UNKNOWN")
        if not plugin_status.get("disk_up_to_date"):
            raise RuntimeError("PLUGIN_STAGE_NOT_VERIFIED")

        lifecycle_id = "plugin_restart_" + uuid.uuid4().hex[:12]
        report = {
            "recovery_id": lifecycle_id,
            "kind": "plugin_restart",
            "host_id": host_id,
            "status": "not_started",
            "workspace": workspace,
            "old_session_id": session.session_id,
            "old_pid": session.pid,
            "project_file": session.project_file,
            "old_adapter_version": session.adapter_version,
            "expected_adapter_version": expected_version,
            "install": install_result,
        }

        if session.adapter_version == expected_version and not force_restart:
            report.update({
                "status": "already_current",
                "adapter_version_verified": True,
            })
            self.recoveries.write(lifecycle_id, report)
            return report

        report["force_restart"] = bool(force_restart)

        executor = self._executors.get(host_id)
        if executor is None:
            raise RuntimeError("ADAPTER_EXECUTOR_NOT_CONNECTED")

        source_command = CommandEnvelope(
            command_id="plugin_restart_" + uuid.uuid4().hex[:16],
            workspace=workspace,
            adapter=host_id,
            session=session.session_id,
            project_file=session.project_file,
            operation="host.plugin_restart",
            arguments={
                "expected_adapter_version": expected_version,
                "source": "bridge_plugin_manager",
            },
            execution=ExecutionOptions(
                verify=True,
                checkpoint="auto",
                dry_run=False,
                budget_seconds=120.0,
                auto_recover=False,
            ),
            risk=RiskLevel.L1,
        )

        checkpoint_id, checkpoint_failure = self._checkpoint_before(
            source_command,
            executor,
            timeout_seconds=120.0,
        )
        if checkpoint_failure is not None or not checkpoint_id:
            report.update({
                "status": "failed",
                "reason": "CHECKPOINT_FAILED",
                "checkpoint_failure": (
                    checkpoint_failure.model_dump(mode="json")
                    if checkpoint_failure is not None
                    else None
                ),
            })
            self.recoveries.write(lifecycle_id, report)
            return report

        report["checkpoint_id"] = checkpoint_id
        checkpoint = self.checkpoints.read(checkpoint_id)
        checkpoint_path = str(checkpoint["checkpoint_path"])
        original_hip = str(checkpoint["original_hip"])
        report["checkpoint_path"] = checkpoint_path
        report["original_hip"] = original_hip

        if not os.path.exists(checkpoint_path):
            report.update({
                "status": "failed",
                "reason": "CHECKPOINT_NOT_FOUND_AFTER_VERIFY",
            })
            self.recoveries.write(lifecycle_id, report)
            return report

        try:
            executable = self.host_process.executable_path(session.pid)
            report["host_executable"] = executable
            report["status"] = "closing"

            close_result = self.host_process.close_gracefully(
                session.pid,
                wait_seconds=close_wait_seconds,
                force_if_needed=True,
            )
            report["close"] = close_result
            if not close_result.get("terminated"):
                raise RuntimeError("HOST_CLOSE_NOT_VERIFIED")

            report["status"] = "clean_install"
            clean_install = self.plugins.install(
                host_id,
                host_running=False,
                force_clean=True,
            )
            report["clean_install"] = clean_install
            if clean_install.get("skipped"):
                raise RuntimeError("PLUGIN_CLEAN_INSTALL_SKIPPED")

            post_clean_status = next(
                item
                for item in self.plugins.status(live_sessions=[])["hosts"]
                if item["id"] == host_id
            )
            if not post_clean_status.get("disk_up_to_date"):
                raise RuntimeError("PLUGIN_CLEAN_INSTALL_NOT_VERIFIED")

            report["status"] = "restarting"
            launch = self.host_process.launch(executable, checkpoint_path)
            report["launch"] = launch

            replacement = self._wait_for_replacement_session(
                adapter=host_id,
                project_file=checkpoint_path,
                old_session_id=session.session_id,
                old_pid=session.pid,
                timeout_seconds=session_wait_seconds,
                expected_adapter_version=expected_version,
            )
            if replacement is None:
                raise RuntimeError("UPDATED_ADAPTER_SESSION_NOT_REGISTERED")

            report["new_session_id"] = replacement.session_id
            report["new_pid"] = replacement.pid
            report["new_adapter_version"] = replacement.adapter_version
            report["status"] = "restoring_project_identity"

            rollback_command = CommandEnvelope(
                command_id="plugin_restart_restore_" + uuid.uuid4().hex[:16],
                workspace=workspace,
                adapter=host_id,
                session=replacement.session_id,
                project_file=replacement.project_file,
                operation="rollback.execute",
                arguments={
                    "checkpoint_path": checkpoint_path,
                    "original_hip": original_hip,
                    "recovery_source_command_id": source_command.command_id,
                },
                execution=ExecutionOptions(
                    verify=True,
                    checkpoint="auto",
                    dry_run=False,
                    budget_seconds=60.0,
                    auto_recover=False,
                ),
                risk=RiskLevel.L2,
            )
            rollback_result = self.execute(rollback_command)
            report["rollback_command_id"] = rollback_command.command_id
            report["rollback_status"] = rollback_result.status.value
            report["rollback_verified"] = bool(
                rollback_result.status == ExecutionStatus.SUCCESS
                and rollback_result.result.get("verified") is not False
            )
            if not report["rollback_verified"]:
                raise RuntimeError("PROJECT_IDENTITY_RESTORE_NOT_VERIFIED")

            report.update({
                "status": "applied",
                "adapter_version_verified": (
                    replacement.adapter_version == expected_version
                ),
                "audit": {
                    "preserved_checkpoint_id": checkpoint_id,
                    "preserved_checkpoint_path": checkpoint_path,
                    "logical_project_restored": original_hip,
                    "forced_close_used": bool(close_result.get("force_used")),
                },
            })
        except Exception as exc:
            report.update({
                "status": "failed",
                "reason": type(exc).__name__,
                "message": str(exc),
            })

        self.recoveries.write(lifecycle_id, report)
        return report

    @staticmethod
    def _resume_command_brief(row: dict) -> dict:
        result = row.get("result") if isinstance(row.get("result"), dict) else None
        failure = result.get("failure") if isinstance(result, dict) else None
        if not isinstance(failure, dict):
            failure = {}
        return {
            "command_id": row.get("command_id"),
            "workspace": row.get("workspace_id"),
            "adapter": row.get("adapter"),
            "session_id": row.get("session_id"),
            "operation": row.get("operation"),
            "status": row.get("status"),
            "terminal": result is not None,
            "failure_code": failure.get("code"),
            "evidence_id": row.get("evidence_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _resume_checkpoint_brief(item: dict | None) -> dict | None:
        if not item:
            return None
        keys = (
            "checkpoint_id", "source_command_id", "workspace", "adapter", "session",
            "original_hip", "checkpoint_path", "verified", "host_executable",
        )
        return {key: item.get(key) for key in keys if key in item}

    @staticmethod
    def _resume_recovery_brief(item: dict | None) -> dict | None:
        if not item:
            return None
        keys = (
            "recovery_id", "kind", "status", "reason", "message", "host_id", "workspace",
            "source_command_id", "source_operation", "old_session_id", "new_session_id",
            "old_pid", "new_pid", "project_file", "original_hip", "checkpoint_id",
            "rollback_command_id", "rollback_status", "rollback_verified", "recorded_at",
        )
        return {key: item.get(key) for key in keys if key in item}

    def project_resume_local(
        self,
        *,
        index: dict,
        project: str | None = None,
        history_limit: int = 100,
    ) -> dict:
        """Aggregate local read-only project state for bridge.project.resume."""
        projects = index.get("projects") if isinstance(index, dict) else None
        if not isinstance(projects, dict) or not projects:
            return {
                "failure": {
                    "code": "PROJECT_INDEX_INVALID",
                    "message": "PROJECT_STATE_INDEX.json has no usable projects mapping",
                }
            }

        active = self.active_sessions()
        requested = str(project or "").strip()
        selected_key: str | None = None

        if requested:
            folded = requested.casefold()
            matches = []
            for key, config in projects.items():
                display_name = str((config or {}).get("display_name") or "")
                if folded in {str(key).casefold(), display_name.casefold()}:
                    matches.append(str(key))
            if len(matches) == 1:
                selected_key = matches[0]
            elif len(matches) > 1:
                return {
                    "failure": {
                        "code": "PROJECT_RESUME_AMBIGUOUS",
                        "message": f"Project selector matches multiple projects: {requested}",
                    },
                    "candidates": matches,
                }
            else:
                return {
                    "failure": {
                        "code": "PROJECT_RESUME_NOT_FOUND",
                        "message": f"Project is not present in PROJECT_STATE_INDEX.json: {requested}",
                    }
                }
        else:
            live_candidates = []
            for key, config in projects.items():
                current_hip = str((config or {}).get("current_hip") or "").strip()
                if not current_hip:
                    continue
                if any(self._same_project(session.project_file, current_hip) for session in active):
                    live_candidates.append(str(key))
            if len(live_candidates) == 1:
                selected_key = live_candidates[0]
            elif len(live_candidates) > 1:
                return {
                    "failure": {
                        "code": "PROJECT_RESUME_AMBIGUOUS",
                        "message": "Multiple indexed projects are live; arguments.project is required",
                    },
                    "candidates": live_candidates,
                }
            elif len(projects) == 1:
                selected_key = str(next(iter(projects)))
            else:
                return {
                    "failure": {
                        "code": "PROJECT_RESUME_PROJECT_REQUIRED",
                        "message": "No unique live indexed project can be inferred; arguments.project is required",
                    },
                    "candidates": list(projects.keys()),
                }

        config = dict(projects[selected_key])
        current_hip = str(config.get("current_hip") or "").strip() or None
        matching_sessions = []
        if current_hip:
            matching_sessions = [
                session for session in active
                if self._same_project(session.project_file, current_hip)
            ]
        matching_sessions.sort(key=lambda item: item.last_seen_at, reverse=True)
        selected_session = matching_sessions[0] if matching_sessions else None

        warnings: list[str] = []
        requires_live_reconcile = False
        if len(matching_sessions) > 1:
            warnings.append("MULTIPLE_LIVE_SESSIONS_FOR_PROJECT")
            requires_live_reconcile = True
        if current_hip and selected_session is None:
            warnings.append("PROJECT_HOST_OFFLINE")
            requires_live_reconcile = True

        history_limit = max(1, min(int(history_limit), 200))
        scan_limit = max(200, history_limit)
        project_rows = []
        if current_hip:
            for row in self.db.list_command_records(limit=scan_limit):
                request = row.get("request") if isinstance(row.get("request"), dict) else {}
                arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
                row_project = str(
                    request.get("project_file") or arguments.get("project_file") or ""
                ).strip()
                if row_project and self._same_project(row_project, current_hip):
                    project_rows.append(row)
                    if len(project_rows) >= history_limit:
                        break

        terminal_rows = [row for row in project_rows if isinstance(row.get("result"), dict)]
        inflight_rows = [row for row in project_rows if not isinstance(row.get("result"), dict)]
        last_terminal = terminal_rows[0] if terminal_rows else None
        last_success = next(
            (row for row in terminal_rows if str(row.get("status")) == "success"),
            None,
        )
        last_failure = next(
            (row for row in terminal_rows if str(row.get("status")) != "success"),
            None,
        )

        checkpoints = []
        if current_hip:
            for item in self.checkpoints.recent(limit=200):
                identities = [
                    str(item.get("original_hip") or "").strip(),
                    str(item.get("project_file") or "").strip(),
                ]
                if any(value and self._same_project(value, current_hip) for value in identities):
                    checkpoints.append(item)

        recoveries = []
        if current_hip:
            for item in self.recoveries.recent(limit=200):
                audit = item.get("audit") if isinstance(item.get("audit"), dict) else {}
                identities = [
                    str(item.get("project_file") or "").strip(),
                    str(item.get("original_hip") or "").strip(),
                    str(audit.get("logical_project_restored") or "").strip(),
                ]
                if any(value and self._same_project(value, current_hip) for value in identities):
                    recoveries.append(item)

        latest_plugin_restart = next(
            (item for item in recoveries if str(item.get("kind") or "") == "plugin_restart"),
            None,
        )
        latest_host_recovery = next(
            (item for item in recoveries if str(item.get("kind") or "") != "plugin_restart"),
            None,
        )

        safe_to_continue = not inflight_rows and not requires_live_reconcile
        return {
            "project": {
                "key": selected_key,
                "display_name": config.get("display_name") or selected_key,
                "current_hip": current_hip,
                "host": config.get("host"),
                "authority": dict(config.get("authority") or {}),
                "recovery_rule": config.get("recovery_rule"),
            },
            "live": {
                "connected": selected_session is not None,
                "adapter": selected_session.adapter if selected_session else None,
                "adapter_version": selected_session.adapter_version if selected_session else None,
                "host_version": selected_session.host_version if selected_session else None,
                "session_id": selected_session.session_id if selected_session else None,
                "pid": selected_session.pid if selected_session else None,
                "project_file": selected_session.project_file if selected_session else current_hip,
                "matching_session_count": len(matching_sessions),
                "write_blocked": bool(self.emergency_stop.write_blocked),
            },
            "execution": {
                "last_terminal": self._resume_command_brief(last_terminal) if last_terminal else None,
                "last_success": self._resume_command_brief(last_success) if last_success else None,
                "last_failure": self._resume_command_brief(last_failure) if last_failure else None,
                "inflight": [self._resume_command_brief(row) for row in inflight_rows[:10]],
                "history_returned": len(project_rows),
                "history_scan_limit": scan_limit,
            },
            "recovery": {
                "latest_checkpoint": self._resume_checkpoint_brief(checkpoints[0]) if checkpoints else None,
                "latest_host_recovery": self._resume_recovery_brief(latest_host_recovery),
                "latest_plugin_restart": self._resume_recovery_brief(latest_plugin_restart),
            },
            "resume": {
                "safe_to_continue": safe_to_continue,
                "requires_live_reconcile": requires_live_reconcile,
                "warnings": warnings,
            },
        }

    def _rollback_failed_execution(
        self,
        command: CommandEnvelope,
        *,
        checkpoint_id: str,
        executor: AdapterExecutor,
        timeout_seconds: float = 60.0,
    ) -> dict:
        report = {
            "attempted": True,
            "checkpoint_id": checkpoint_id,
            "status": "failed",
            "verified": False,
        }
        try:
            checkpoint = self.checkpoints.read(checkpoint_id)
            checkpoint_path = str(checkpoint.get("checkpoint_path") or "").strip()
            original_hip = str(checkpoint.get("original_hip") or command.project_file or "").strip()
            if not checkpoint_path:
                raise RuntimeError("CHECKPOINT_PATH_MISSING")
            if checkpoint.get("verified") is False:
                raise RuntimeError("CHECKPOINT_NOT_VERIFIED")
            if not command.session:
                raise RuntimeError("SESSION_REQUIRED_FOR_ROLLBACK")
            session = self.sessions.get(command.session)
            rollback = CommandEnvelope(
                command_id=command.command_id + ":auto_rollback:" + uuid.uuid4().hex[:6],
                workspace=command.workspace,
                adapter=command.adapter,
                session=command.session,
                project_file=session.project_file,
                operation="rollback.execute",
                arguments={
                    "checkpoint_path": checkpoint_path,
                    "original_hip": original_hip,
                },
                execution=ExecutionOptions(
                    verify=True,
                    checkpoint="none",
                    dry_run=False,
                    budget_seconds=max(1.0, float(timeout_seconds)),
                    auto_recover=False,
                ),
                risk=RiskLevel.L2,
            )
            if isinstance(executor, QueueAdapterExecutor):
                rollback_result = executor.execute(
                    rollback,
                    timeout=max(1.0, float(timeout_seconds)),
                )
            else:
                rollback_result = executor.execute(rollback)
            verified = bool(
                rollback_result.status == ExecutionStatus.SUCCESS
                and rollback_result.result.get("verified") is not False
            )
            report.update({
                "status": "success" if verified else "failed",
                "verified": verified,
                "rollback_command_id": rollback.command_id,
                "rollback_status": rollback_result.status.value,
                "rollback_failure_code": (
                    rollback_result.failure.code
                    if rollback_result.failure is not None
                    else None
                ),
                "original_hip": original_hip,
                "checkpoint_path": checkpoint_path,
            })
        except Exception as exc:
            report.update({
                "status": "failed",
                "verified": False,
                "failure_code": "AUTO_ROLLBACK_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
            })
        return report

    def execute(self, command: CommandEnvelope) -> ExecutionResult:
        if self.db.command_exists(command.command_id):
            raise DuplicateCommandError(command.command_id)
        self.db.insert_command(command)

        try:
            self.workspaces.get(command.workspace)
        except KeyError:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.WORKSPACE, code="WORKSPACE_NOT_FOUND"),
            )
            return self._finalize(command, result)

        cap, cap_error = self._capability(command)
        if cap is None:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.ADAPTER, code=cap_error),
            )
            return self._finalize(command, result)

        if cap.write and command.session and not command.project_file:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.CORE, code="PROJECT_FILE_REQUIRED"),
            )
            return self._finalize(command, result)

        if cap.write and command.session:
            session = self.sessions.get(command.session)
            if not self._same_project(session.project_file, command.project_file):
                result = ExecutionResult(
                    command_id=command.command_id,
                    status=ExecutionStatus.CONFLICT,
                    failure=FailureInfo(origin=FailureOrigin.CORE, stage="precondition", code="PROJECT_FILE_MISMATCH"),
                    last_known_state={"session_project_file": session.project_file},
                )
                return self._finalize(command, result)

        if cap.write and self.emergency_stop.write_blocked:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.CORE, code="EMERGENCY_STOP"),
            )
            return self._finalize(command, result)

        executor = self._executors.get(command.adapter)
        if executor is None:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                failure=FailureInfo(origin=FailureOrigin.ADAPTER, code="ADAPTER_EXECUTOR_NOT_CONNECTED"),
            )
            return self._finalize(command, result)

        budget = self.execution_policy.resolve(command)
        old_session = self.sessions.get(command.session) if command.session else None
        checkpoint_id = None
        checkpoint_required = not bool(getattr(cap, "manages_checkpoint", False)) and (
            (
                cap.write and cap.risk in (RiskLevel.L2, RiskLevel.L3)
            ) or (
                bool(budget and budget.auto_recover)
                and bool(getattr(cap, "long_running", False))
            )
        )
        if checkpoint_required and command.operation not in {"checkpoint.create", "rollback.execute"}:
            checkpoint_timeout = None
            if isinstance(executor, QueueAdapterExecutor) and budget is not None:
                checkpoint_timeout = min(
                    float(getattr(executor, "timeout", 30.0)),
                    float(budget.seconds),
                )
            checkpoint_id, checkpoint_failure = self._checkpoint_before(
                command,
                executor,
                timeout_seconds=checkpoint_timeout,
            )
            if checkpoint_failure is not None:
                if (
                    budget is not None
                    and budget.auto_recover
                    and old_session is not None
                    and checkpoint_failure.failure is not None
                    and checkpoint_failure.failure.code in {
                        "ADAPTER_TIMEOUT",
                        "EXECUTION_BUDGET_EXCEEDED",
                    }
                ):
                    recovery = self.force_recover_host(
                        host_id=command.adapter,
                        session_id=old_session.session_id,
                        workspace=command.workspace,
                        checkpoint_id=None,
                        allow_saved_project_fallback=False,
                        source_command_id=command.command_id,
                        source_operation=command.operation,
                    )
                    checkpoint_failure.result.setdefault("_bridge", {})["recovery"] = recovery
                    if recovery.get("status") in {"recovered", "restarted_checkpoint_safe"}:
                        checkpoint_failure.last_known_state.update({
                            "host": recovery.get("status"),
                            "recovery_id": recovery.get("recovery_id"),
                            "new_session_id": recovery.get("new_session_id"),
                            "new_pid": recovery.get("new_pid"),
                        })
                return self._finalize(command, checkpoint_failure)

        try:
            if isinstance(executor, QueueAdapterExecutor):
                result = executor.execute(command, timeout=None if budget is None else budget.seconds)
            else:
                result = executor.execute(command)
        except Exception as exc:
            result = ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER,
                    code="ADAPTER_EXECUTION_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
        if (
            checkpoint_id is not None
            and bool(getattr(cap, "rollback_after_execution", False))
            and not (
                result.failure is not None
                and result.failure.code == "EXECUTION_BUDGET_EXCEEDED"
            )
        ):
            original_status = result.status.value
            rollback_report = self._rollback_failed_execution(
                command,
                checkpoint_id=checkpoint_id,
                executor=executor,
                timeout_seconds=60.0,
            )
            bridge_meta = result.result.setdefault("_bridge", {})
            bridge_meta["validation_original_status"] = original_status
            bridge_meta["validation_rollback"] = rollback_report
            if rollback_report.get("verified"):
                result.last_known_state.update({
                    "project_state": "rolled_back_to_checkpoint",
                    "rollback_checkpoint_id": checkpoint_id,
                    "validation_only": True,
                })
            elif result.status == ExecutionStatus.SUCCESS:
                result.status = ExecutionStatus.FAILED
                result.failure = FailureInfo(
                    origin=FailureOrigin.CORE,
                    stage="rollback",
                    code="VALIDATION_ROLLBACK_FAILED",
                    message="Validation execution completed but checkpoint restoration was not verified.",
                )

        if (
            checkpoint_id is not None
            and bool(getattr(cap, "rollback_on_failure", False))
            and result.status == ExecutionStatus.FAILED
            and not (
                result.failure is not None
                and result.failure.code == "EXECUTION_BUDGET_EXCEEDED"
            )
        ):
            rollback_report = self._rollback_failed_execution(
                command,
                checkpoint_id=checkpoint_id,
                executor=executor,
                timeout_seconds=60.0,
            )
            result.result.setdefault("_bridge", {})["failure_rollback"] = rollback_report
            if rollback_report.get("verified"):
                result.last_known_state.update({
                    "project_state": "rolled_back_to_checkpoint",
                    "rollback_checkpoint_id": checkpoint_id,
                })

        if (
            budget is not None
            and budget.auto_recover
            and result.failure is not None
            and result.failure.code == "EXECUTION_BUDGET_EXCEEDED"
            and old_session is not None
        ):
            recovery = self._recover_budget_exceeded(
                command,
                checkpoint_id=checkpoint_id,
                old_session=old_session,
            )
            result.result.setdefault("_bridge", {})["recovery"] = recovery
            if recovery.get("status") == "recovered":
                result.last_known_state.update({
                    "host": "recovered",
                    "recovery_id": recovery.get("recovery_id"),
                    "new_session_id": recovery.get("new_session_id"),
                    "new_pid": recovery.get("new_pid"),
                })

        return self._finalize(command, result, checkpoint_id=checkpoint_id, write=cap.write)

    def rollback_command(self, command_id: str) -> ExecutionResult:
        snapshot = self.snapshots.read(command_id)
        original_row = self.db.get_command(command_id)
        if original_row is None:
            raise KeyError(command_id)
        req = original_row["request"]
        checkpoint_id = snapshot.get("checkpoint_id")
        rb_id = "rb_" + command_id + "_" + uuid.uuid4().hex[:8]

        if checkpoint_id:
            cp = self.checkpoints.read(checkpoint_id)
            command = CommandEnvelope(
                command_id=rb_id,
                workspace=req["workspace"],
                adapter=req["adapter"],
                session=req.get("session"),
                project_file=req.get("project_file"),
                operation="rollback.execute",
                arguments={
                    "checkpoint_path": cp["checkpoint_path"],
                    "original_hip": cp["original_hip"],
                },
                risk=RiskLevel.L2,
            )
            return self.execute(command)

        before, after = snapshot.get("before"), snapshot.get("after")
        op = snapshot["operation"]
        args = dict(snapshot["arguments"])
        if op in ("code.write", "code.patch"):
            command = CommandEnvelope(
                command_id=rb_id, workspace=req["workspace"], adapter=req["adapter"], session=req.get("session"), project_file=req.get("project_file"),
                operation="code.write",
                arguments={"path": args["path"], "parameter": args["parameter"], "text": before["text"], "expected_hash": after["hash"]},
            )
        elif op == "parm.write":
            command = CommandEnvelope(
                command_id=rb_id, workspace=req["workspace"], adapter=req["adapter"], session=req.get("session"), project_file=req.get("project_file"),
                operation="parm.write",
                arguments={"path": args["path"], "parameter": args["parameter"], "value": before["value"], "expected_hash": after["hash"]},
            )
        else:
            return ExecutionResult(
                command_id=rb_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.CORE, code="LIGHTWEIGHT_ROLLBACK_NOT_SUPPORTED"),
            )
        return self.execute(command)
