from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ExecutionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verify: bool = True
    checkpoint: str = "auto"
    dry_run: bool = False
    budget_seconds: float | None = Field(default=None, ge=1.0, le=7200.0)
    auto_recover: bool | None = None


class CommandEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str = "bridge/1"
    command_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    workspace: str = Field(min_length=1, max_length=128)
    adapter: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    operation: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    session: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_file: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionOptions = Field(default_factory=ExecutionOptions)
    risk: RiskLevel = RiskLevel.L1

    @field_validator("workspace", "adapter", "operation", "command_id")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
