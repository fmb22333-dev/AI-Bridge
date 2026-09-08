from pydantic import BaseModel, ConfigDict, Field
from .command import RiskLevel


class CapabilityDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    write: bool
    risk: RiskLevel
    rollback: bool = False
    verification: bool = True
    long_running: bool = False
    manages_checkpoint: bool = False
    rollback_on_failure: bool = False
    rollback_after_execution: bool = False
    tested_host_versions: list[str] = Field(default_factory=list)
