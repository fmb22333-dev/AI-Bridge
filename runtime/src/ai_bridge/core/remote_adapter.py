from ai_bridge.core.adapter_bus import AdapterCommandBus
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin


class QueueAdapterExecutor:
    def __init__(self, bus: AdapterCommandBus, *, timeout: float = 30.0) -> None:
        self.bus = bus
        self.timeout = timeout

    def execute(self, command: CommandEnvelope, *, timeout: float | None = None) -> ExecutionResult:
        if not command.session:
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(origin=FailureOrigin.ADAPTER, code="SESSION_REQUIRED"),
            )
        effective_timeout = self.timeout if timeout is None else max(1.0, float(timeout))
        busy_getter = getattr(self.bus, "busy_source", None)
        busy = busy_getter(command.session) if callable(busy_getter) else None
        if busy is not None:
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.CONFLICT,
                failure=FailureInfo(
                    origin=FailureOrigin.CORE,
                    stage="precondition",
                    code="SESSION_BUSY_UNKNOWN",
                    message="A prior Host operation exceeded its execution budget and has not produced a terminal Adapter result yet.",
                    retryable=False,
                ),
                last_known_state={"session": command.session, "session_state": "busy_unknown", **busy},
            )
        result = self.bus.submit(command.session, command, timeout=effective_timeout)
        if result is None:
            budgeted = timeout is not None
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.UNKNOWN,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER,
                    stage="execution_budget" if budgeted else "adapter_wait",
                    code="EXECUTION_BUDGET_EXCEEDED" if budgeted else "ADAPTER_TIMEOUT",
                    message=(
                        f"Host operation exceeded its {effective_timeout:g}s execution budget."
                        if budgeted
                        else f"Adapter result was not returned within {effective_timeout:g}s."
                    ),
                    retryable=False if budgeted else True,
                ),
                last_known_state={
                    "session": command.session,
                    "budget_seconds": effective_timeout if budgeted else None,
                    "host_operation_may_still_be_running": True,
                    "session_state": "busy_unknown",
                },
            )
        return result
