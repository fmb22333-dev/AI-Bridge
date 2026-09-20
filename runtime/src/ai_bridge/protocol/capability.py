from pydantic import BaseModel, ConfigDict, Field
from .command import RiskLevel


class CapabilityDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    write: bool
    # Any side effect remains a write for emergency-stop and identity guards.
    # host_mutation only classifies whether the operation changes persistent Host
    # project/session state and therefore needs a pre-dispatch Host checkpoint.
    host_mutation: bool = True
    risk: RiskLevel
    rollback: bool = False
    verification: bool = True
    # Long-running operations may exceed project budgets even when they do not
    # directly mutate the scene. If automatic recovery is enabled, Bridge
    # creates a host checkpoint before dispatching these operations.
    long_running: bool = False
    # Composite operations may own a stronger internal checkpoint lifecycle.
    # When true, Core must not create a redundant outer checkpoint before dispatch.
    manages_checkpoint: bool = False
    # Explicit compound-operation contract: when execution returns FAILED after
    # Core created a verified pre-command checkpoint, Core immediately restores
    # that checkpoint. DENIED/CONFLICT results are not auto-rolled back because
    # they are precondition outcomes and should not have mutated the host.
    rollback_on_failure: bool = False
    # Validation-only compound operations may execute real Host mutations only
    # inside a verified checkpoint boundary and must restore that checkpoint
    # after every non-budget terminal outcome, including success.
    rollback_after_execution: bool = False
    tested_host_versions: list[str] = Field(default_factory=list)
