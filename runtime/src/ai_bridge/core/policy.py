from dataclasses import dataclass
from pathlib import Path

from ai_bridge.core.workspace import Workspace
from ai_bridge.protocol.capability import CapabilityDescriptor


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str = "ALLOW"
    message: str = ""


class PolicyEngine:
    """Deterministic policy checks only. No intent inference."""

    def authorize_path(
        self,
        workspace: Workspace,
        target: Path,
        capability: CapabilityDescriptor,
    ) -> PolicyDecision:
        if not workspace.contains(target):
            return PolicyDecision(False, "WRITE_OUTSIDE_WORKSPACE" if capability.write else "READ_OUTSIDE_WORKSPACE")
        return PolicyDecision(True)
