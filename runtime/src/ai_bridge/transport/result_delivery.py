from __future__ import annotations

import json
from typing import Any

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult


COMPACT_THRESHOLD_CHARS = 12000
SUMMARY_BUDGET_CHARS = 5000
VALUE_BUDGET_CHARS = 1600


def _safe_component(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    cooked = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw)
    return cooked[:128] or fallback


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        return VALUE_BUDGET_CHARS + 1


def _v5_ref(transport, command_id: str) -> dict | None:
    refs = getattr(transport, "_comment_refs", None)
    if not isinstance(refs, dict):
        return None
    ref = refs.get(command_id)
    if not isinstance(ref, dict) or ref.get("mode") != "issue_channel_v5":
        return None
    return ref


def _full_result_path(transport, ref: dict, command_id: str) -> str:
    channel = _safe_component(str(ref.get("channel_id") or ""), "unknown")
    command = _safe_component(command_id, "command")
    return transport._bus_path(
        f"results/{transport.config.bridge_id}/channels/{channel}/{command}.json"
    )


def _command_summary(command: CommandEnvelope) -> dict:
    return {
        "protocol": command.protocol,
        "command_id": command.command_id,
        "workspace": command.workspace,
        "adapter": command.adapter,
        "operation": command.operation,
        "session": command.session,
        "project_file": command.project_file,
        "argument_keys": sorted(str(key) for key in command.arguments.keys()),
        "execution": {
            "verify": command.execution.verify,
            "checkpoint": command.execution.checkpoint,
            "dry_run": command.execution.dry_run,
            "budget_seconds": command.execution.budget_seconds,
            "auto_recover": command.execution.auto_recover,
        },
        "risk": command.risk.value,
    }


def _compact_payload(
    payload: dict,
    *,
    full_result_ref: str,
    full_result_chars: int,
) -> dict:
    summary: dict[str, Any] = {}
    omitted: list[str] = []
    used = 0

    for key, value in payload.items():
        if key == "_bridge":
            continue
        size = _json_chars(value)
        if size <= VALUE_BUDGET_CHARS and used + size <= SUMMARY_BUDGET_CHARS:
            summary[key] = value
            used += size
        else:
            omitted.append(str(key))

    source_bridge = payload.get("_bridge")
    bridge_summary: dict[str, Any] = {}
    if isinstance(source_bridge, dict):
        for key in ("execution_budget", "checkpoint_id", "recovery", "timing"):
            if key not in source_bridge:
                continue
            value = source_bridge[key]
            if _json_chars(value) <= VALUE_BUDGET_CHARS:
                bridge_summary[key] = value

    bridge_summary["delivery"] = {
        "mode": "summary",
        "full_result_ref": full_result_ref,
        "full_result_chars": full_result_chars,
        "omitted_result_keys": omitted,
    }
    summary["_bridge"] = bridge_summary
    return summary


def prepare_result_delivery(
    transport,
    command: CommandEnvelope,
    result: ExecutionResult,
) -> ExecutionResult:
    ref = _v5_ref(transport, result.command_id)
    if ref is None or not hasattr(transport, "_publish_json"):
        return result

    full_result_path = _full_result_path(transport, ref, result.command_id)
    full_payload = {
        "schema_version": "1.0",
        "bridge_id": transport.config.bridge_id,
        "channel_id": ref.get("channel_id"),
        "generation": ref.get("generation"),
        "command_id": result.command_id,
        "command": command.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "delivery": {"mode": "full"},
    }
    full_chars = _json_chars(full_payload)

    if full_chars <= COMPACT_THRESHOLD_CHARS:
        return result

    try:
        transport._publish_json(
            full_result_path,
            full_payload,
            message=f"bridge full result {result.command_id}",
            create_first=True,
        )
    except Exception:
        return result

    compact = result.model_copy(deep=True)
    compact.result = _compact_payload(
        result.result,
        full_result_ref=full_result_path,
        full_result_chars=full_chars,
    )
    ref["command"] = _command_summary(command)
    return compact
