from __future__ import annotations

from typing import Any


STATUSES = {"success", "failed", "skipped", "denied", "conflict"}


def base(command_id: str, session_id: str) -> dict:
    return {
        "command_id": str(command_id),
        "status": "success",
        "stages": {},
        "result": {},
        "failure": None,
        "rollback_available": False,
        "last_known_state": {"host": "alive", "session": str(session_id)},
        "evidence_id": None,
    }


def _contract_failure(message: str, *, status: str | None = None) -> dict:
    context = {}
    if status is not None:
        context["invalid_status"] = status
    return {
        "origin": "adapter",
        "stage": "outcome_contract",
        "code": "OUTCOME_CONTRACT_INVALID",
        "category": "adapter_contract",
        "message": message,
        "retryable": False,
        "suggestion": "Inspect the compound operation result construction before retrying.",
        "context": context,
    }


def normalize(value: Any) -> dict:
    if not isinstance(value, dict):
        out = base("unknown", "")
        out["status"] = "failed"
        out["stages"] = {"EXECUTE": "FAILED"}
        out["failure"] = _contract_failure(
            f"Dispatcher returned {type(value).__name__}, expected object."
        )
        return out

    out = dict(value)
    out.setdefault("command_id", "unknown")
    status = str(out.get("status") or "success").strip().lower()
    if status not in STATUSES:
        original = status
        status = "failed"
        out["failure"] = _contract_failure(
            f"Unsupported outcome status: {original!r}.",
            status=original,
        )
        out["stages"] = {"EXECUTE": "FAILED"}
    out["status"] = status

    if not isinstance(out.get("stages"), dict):
        out["stages"] = {}
    if not isinstance(out.get("result"), dict):
        payload = out.get("result")
        out["result"] = {"value": payload} if payload is not None else {}
    out["rollback_available"] = bool(out.get("rollback_available", False))
    if not isinstance(out.get("last_known_state"), dict):
        out["last_known_state"] = {}
    out.setdefault("evidence_id", None)

    failure = out.get("failure")
    if status == "success":
        out["failure"] = None
    elif status in {"failed", "denied", "conflict"}:
        if not isinstance(failure, dict) or not str(failure.get("code") or "").strip():
            out["failure"] = _contract_failure(
                f"Outcome status {status!r} requires a structured failure object."
            )
            if status != "failed":
                out["failure"]["context"]["original_status"] = status
    elif failure is not None and not isinstance(failure, dict):
        out["failure"] = None

    return out
