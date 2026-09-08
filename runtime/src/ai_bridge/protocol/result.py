from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class FailureOrigin(str, Enum):
    CORE = "core"
    ADAPTER = "adapter"
    HOST = "host"
    AI_CODE = "ai_code"
    WORKSPACE = "workspace"
    BUILD = "build"
    UNKNOWN = "unknown"


class FailureInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    origin: FailureOrigin
    stage: str | None = None
    code: str | None = None
    message: str | None = None
    category: str | None = None
    exception_type: str | None = None
    retryable: bool | None = None
    suggestion: str | None = None
    knowledge_rule: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    host_errors: list[str] = Field(default_factory=list)
    host_warnings: list[str] = Field(default_factory=list)
    supported_operations: list[str] = Field(default_factory=list)
    underlying: Any | None = None


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=1)
    status: ExecutionStatus
    stages: dict[str, str] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    failure: FailureInfo | None = None
    rollback_available: bool = False
    last_known_state: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None
