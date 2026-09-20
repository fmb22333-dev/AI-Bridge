from __future__ import annotations

from ai_bridge.adapters.registry import AdapterDescriptor
from ai_bridge.persistence.scene_captures import SceneCaptureStore
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import (
    ExecutionResult,
    ExecutionStatus,
    FailureInfo,
    FailureOrigin,
)


ADAPTER_NAME = "scene_store"


def descriptor() -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter=ADAPTER_NAME,
        adapter_version="1.0",
        endpoint="local://scene-store",
        capabilities=[
            CapabilityDescriptor(
                name="scene.query",
                version="1.0",
                write=False,
                risk=RiskLevel.L1,
            )
        ],
    )


class SceneStoreExecutor:
    def __init__(self, *, store: SceneCaptureStore) -> None:
        self.store = store

    def execute(self, command: CommandEnvelope) -> ExecutionResult:
        if command.operation != "scene.query":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(
                    origin=FailureOrigin.CORE,
                    code="CAPABILITY_NOT_SUPPORTED",
                    message=f"Unsupported scene store operation: {command.operation}",
                ),
            )

        try:
            payload = self.store.query(dict(command.arguments))
        except FileNotFoundError as exc:
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                failure=FailureInfo(
                    origin=FailureOrigin.CORE,
                    code="SCENE_CAPTURE_NOT_FOUND",
                    message=str(exc),
                ),
            )
        except (TypeError, ValueError) as exc:
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                failure=FailureInfo(
                    origin=FailureOrigin.CORE,
                    code="INVALID_SCENE_QUERY",
                    message=str(exc),
                ),
            )

        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result=payload,
            stages={"QUERY": "VERIFIED"},
        )
