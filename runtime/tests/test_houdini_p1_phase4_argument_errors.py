from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import error_ops, knowledge_registry


def test_phase4_argument_invalid_has_normalized_error_code():
    rule = knowledge_registry.match_error_text(
        "ARGUMENT_INVALID: parameters must be a non-empty list"
    )
    assert rule is not None
    assert rule["code"] == "ARGUMENT_INVALID"
    assert rule["category"] == "invalid_argument"

    failure = error_ops.classify_exception(
        ValueError("ARGUMENT_INVALID: parameters must be a non-empty list"),
        operation="parm.batch_read",
        arguments={"path": "/obj/geo1/IMPORT_ANIM", "parameters": []},
    )
    assert failure["code"] == "ARGUMENT_INVALID"
    assert failure["category"] == "invalid_argument"
    assert failure["knowledge_rule"] == "bridge.argument_invalid"
    assert failure["retryable"] is False
