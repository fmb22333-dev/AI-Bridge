from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import outcome


def test_success_contract_clears_failure_and_preserves_shape():
    result = outcome.normalize({
        "command_id": "cmd-1",
        "status": "success",
        "stages": {"READBACK": "VERIFIED"},
        "result": {"ok": True},
        "failure": {"code": "STALE"},
        "rollback_available": 0,
        "last_known_state": {"host": "alive"},
    })
    assert result["status"] == "success"
    assert result["failure"] is None
    assert result["rollback_available"] is False
    assert result["result"] == {"ok": True}


def test_failed_denied_conflict_require_structured_failure():
    for status in ("failed", "denied", "conflict"):
        result = outcome.normalize({
            "command_id": "cmd-2",
            "status": status,
            "stages": {},
            "result": {},
            "failure": None,
        })
        assert result["status"] == status
        assert result["failure"]["code"] == "OUTCOME_CONTRACT_INVALID"


def test_skipped_is_first_class_terminal_without_fake_failure():
    result = outcome.normalize({
        "command_id": "cmd-3",
        "status": "skipped",
        "stages": {"EXECUTE": "NOT_RUN"},
        "result": {"reason": "precondition_not_applicable"},
        "failure": None,
    })
    assert result["status"] == "skipped"
    assert result["failure"] is None


def test_unknown_status_fails_closed():
    result = outcome.normalize({
        "command_id": "cmd-4",
        "status": "maybe",
        "result": {},
    })
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "OUTCOME_CONTRACT_INVALID"
    assert result["failure"]["context"]["invalid_status"] == "maybe"


def test_non_dict_dispatch_result_fails_closed():
    result = outcome.normalize("bad")
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "OUTCOME_CONTRACT_INVALID"
