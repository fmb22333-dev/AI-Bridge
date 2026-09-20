from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult


COMPACT_THRESHOLD_CHARS = 12000
FULL_COMMENT_LIMIT_CHARS = 50000
SUMMARY_BUDGET_CHARS = 6500
STRING_PREFIX_CHARS = 700
STRING_SUFFIX_CHARS = 160
LIST_SAMPLE_HEAD = 6
LIST_SAMPLE_TAIL = 2
MAX_DEPTH = 8
MAX_DICT_ITEMS = 80

PRIORITY_KEYS = {
    "status",
    "state",
    "code",
    "message",
    "error",
    "failure",
    "count",
    "total",
    "total_matches",
    "hash",
    "sha",
    "digest",
    "checkpoint_id",
    "recovery",
    "timing",
    "path",
    "project_file",
    "session_id",
}


@dataclass
class CompactTrace:
    truncated_paths: list[str] = field(default_factory=list)
    omitted_paths: list[str] = field(default_factory=list)


@dataclass
class CompactBudget:
    remaining: int = SUMMARY_BUDGET_CHARS

    def spend(self, value: Any) -> bool:
        size = _json_chars(value)
        if size > self.remaining:
            return False
        self.remaining -= size
        return True


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        return SUMMARY_BUDGET_CHARS + 1


def _v5_ref(transport, command_id: str) -> dict | None:
    refs = getattr(transport, "_comment_refs", None)
    if not isinstance(refs, dict):
        return None
    ref = refs.get(command_id)
    if not isinstance(ref, dict) or ref.get("mode") != "issue_channel_v5":
        return None
    return ref


def _full_result_path(transport, ref: dict, command_id: str) -> str:
    channel = str(ref.get("channel_id") or "unknown")
    channel = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in channel)[:128] or "unknown"
    command = str(command_id or "command")
    command = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in command)[:128] or "command"
    return transport._bus_path(f"results/{transport.config.bridge_id}/channels/{channel}/{command}.json")


def _full_reference_result(result: ExecutionResult, *, full_result_ref: str, full_result_chars: int) -> ExecutionResult:
    source = result.result if isinstance(result.result, dict) else {}
    payload = {key: value for key, value in source.items() if key not in {"terminal_result", "_bridge"}}
    bridge = {}
    source_bridge = source.get("_bridge")
    if isinstance(source_bridge, dict):
        for key in ("execution_budget", "checkpoint_id", "recovery", "timing"):
            if key in source_bridge:
                bridge[key] = source_bridge[key]
    bridge["delivery"] = {
        "mode": "full_reference",
        "full_result_source": "github_contents_on_demand",
        "full_result_ref": full_result_ref,
        "full_result_chars": full_result_chars,
    }
    payload["_bridge"] = bridge
    compact = result.model_copy(deep=True)
    compact.result = payload
    return compact


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


def _path_child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    text = str(key)
    if text.replace("_", "").isalnum():
        return f"{path}.{text}"
    return f"{path}[{json.dumps(text, ensure_ascii=False)}]"


def _type_descriptor(value: Any, *, reason: str) -> dict:
    if isinstance(value, dict):
        return {"_omitted": True, "type": "dict", "keys": len(value), "reason": reason}
    if isinstance(value, list):
        return {"_omitted": True, "type": "list", "count": len(value), "reason": reason}
    if isinstance(value, str):
        return {"_omitted": True, "type": "string", "chars": len(value), "reason": reason}
    return {"_omitted": True, "type": type(value).__name__, "reason": reason}


def _compact_value(
    value: Any,
    *,
    path: str,
    depth: int,
    budget: CompactBudget,
    trace: CompactTrace,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if budget.spend(value):
            return value
        trace.omitted_paths.append(path)
        return _type_descriptor(value, reason="summary_budget")

    if isinstance(value, str):
        if len(value) <= STRING_PREFIX_CHARS + STRING_SUFFIX_CHARS and budget.spend(value):
            return value
        if len(value) <= STRING_PREFIX_CHARS + STRING_SUFFIX_CHARS:
            trace.omitted_paths.append(path)
            return _type_descriptor(value, reason="summary_budget")
        compact = {
            "_truncated": True,
            "chars": len(value),
            "prefix": value[:STRING_PREFIX_CHARS],
            "suffix": value[-STRING_SUFFIX_CHARS:] if STRING_SUFFIX_CHARS else "",
        }
        if not budget.spend(compact):
            compact = {
                "_truncated": True,
                "chars": len(value),
                "prefix": value[: min(160, STRING_PREFIX_CHARS)],
                "suffix": value[-min(64, STRING_SUFFIX_CHARS):] if STRING_SUFFIX_CHARS else "",
            }
            budget.spend(compact)
        trace.truncated_paths.append(path)
        return compact

    if depth >= MAX_DEPTH:
        trace.omitted_paths.append(path)
        compact = _type_descriptor(value, reason="max_depth")
        budget.spend(compact)
        return compact

    if isinstance(value, list):
        if len(value) <= LIST_SAMPLE_HEAD + LIST_SAMPLE_TAIL and _json_chars(value) <= min(budget.remaining, 1400):
            compact_list = []
            for index, item in enumerate(value):
                compact_list.append(
                    _compact_value(
                        item,
                        path=_path_child(path, index),
                        depth=depth + 1,
                        budget=budget,
                        trace=trace,
                    )
                )
            return compact_list

        trace.truncated_paths.append(path)
        head_count = min(LIST_SAMPLE_HEAD, len(value))
        tail_count = min(LIST_SAMPLE_TAIL, max(0, len(value) - head_count))
        compact = {"_summary": "list", "count": len(value), "head": [], "tail": []}
        budget.spend({"_summary": "list", "count": len(value)})
        for index in range(head_count):
            compact["head"].append(
                _compact_value(
                    value[index],
                    path=_path_child(path, index),
                    depth=depth + 1,
                    budget=budget,
                    trace=trace,
                )
            )
        if tail_count:
            start = len(value) - tail_count
            for index in range(start, len(value)):
                compact["tail"].append(
                    _compact_value(
                        value[index],
                        path=_path_child(path, index),
                        depth=depth + 1,
                        budget=budget,
                        trace=trace,
                    )
                )
        return compact

    if isinstance(value, dict):
        keys = list(value.keys())
        ordered = sorted(keys, key=lambda key: (0 if str(key) in PRIORITY_KEYS else 1, str(key)))
        if len(ordered) > MAX_DICT_ITEMS:
            for key in ordered[MAX_DICT_ITEMS:]:
                trace.omitted_paths.append(_path_child(path, key))
            ordered = ordered[:MAX_DICT_ITEMS]

        compact: dict[str, Any] = {}
        for key in ordered:
            child_path = _path_child(path, key)
            if budget.remaining <= 128 and str(key) not in PRIORITY_KEYS:
                trace.omitted_paths.append(child_path)
                compact[str(key)] = _type_descriptor(value[key], reason="summary_budget")
                continue
            compact[str(key)] = _compact_value(
                value[key],
                path=child_path,
                depth=depth + 1,
                budget=budget,
                trace=trace,
            )
        return compact

    text = str(value)
    return _compact_value(text, path=path, depth=depth, budget=budget, trace=trace)


def _structured_compact_payload(payload: dict, *, command_id: str) -> dict:
    trace = CompactTrace()
    budget = CompactBudget()
    summary = _compact_value(
        payload,
        path="$",
        depth=0,
        budget=budget,
        trace=trace,
    )
    if not isinstance(summary, dict):
        summary = {"value": summary}

    source_bridge = payload.get("_bridge")
    bridge_summary = summary.get("_bridge")
    if not isinstance(bridge_summary, dict):
        bridge_summary = {}
    if isinstance(source_bridge, dict):
        for key in ("execution_budget", "checkpoint_id", "recovery", "timing"):
            if key in source_bridge and key not in bridge_summary:
                bridge_summary[key] = _compact_value(
                    source_bridge[key],
                    path=f"$._bridge.{key}",
                    depth=1,
                    budget=budget,
                    trace=trace,
                )

    bridge_summary["delivery"] = {
        "mode": "structured_summary",
        "full_result_source": "local_command_result",
        "recovery": {
            "adapter": "bridge_transport",
            "operation": "command.status",
            "arguments": {"command_id": command_id, "include_result": True},
        },
        "truncated_paths": trace.truncated_paths,
        "omitted_paths": trace.omitted_paths,
        "summary_budget_chars": SUMMARY_BUDGET_CHARS,
    }
    summary["_bridge"] = bridge_summary
    return summary


def _capability_row_compact(row: dict) -> dict:
    kind = str(row.get("kind") or "")
    compact = {
        "kind": kind,
        "name": row.get("name"),
        "version": row.get("version"),
    }
    if kind == "recipe":
        compact.update({
            "state": row.get("promotion_state") or "unknown",
            "execution_authorized": bool(row.get("execution_authorized")),
            "description": row.get("description"),
            "required": list(row.get("required") or []),
            "optional": list(row.get("optional") or []),
            "superseded_by": row.get("superseded_by"),
        })
    else:
        schema = row.get("argument_schema") if isinstance(row.get("argument_schema"), dict) else {}
        required = schema.get("required")
        if isinstance(required, dict):
            required = list(required.keys())
        elif not isinstance(required, list):
            required = []
        guidance = row.get("guidance") if isinstance(row.get("guidance"), dict) else {}
        compact.update({
            "state": guidance.get("state") or row.get("availability") or "implemented",
            "availability": row.get("availability"),
            "routable": row.get("routable"),
            "risk": row.get("risk"),
            "write": row.get("write"),
            "required": required,
            "description": schema.get("summary") or guidance.get("suggestion"),
        })
    return {key: value for key, value in compact.items() if value is not None}


def _compact_capability_search(result: ExecutionResult) -> ExecutionResult:
    source = result.result if isinstance(result.result, dict) else {}
    compact_payload: dict[str, Any] = {}
    for key in (
        "query",
        "count",
        "total_matches",
        "matching",
        "adapter",
        "adapter_version",
        "host_version",
        "project_file",
        "integrity",
    ):
        if key in source:
            compact_payload[key] = source[key]

    rows = source.get("results")
    if isinstance(rows, list):
        compact_payload["results"] = [
            _capability_row_compact(row)
            for row in rows
            if isinstance(row, dict)
        ]
    related = source.get("related_knowledge")
    if isinstance(related, list):
        compact_payload["related_knowledge_count"] = len(related)

    bridge_summary: dict[str, Any] = {}
    source_bridge = source.get("_bridge")
    if isinstance(source_bridge, dict):
        for key in ("execution_budget", "checkpoint_id", "recovery", "timing", "capability_authority", "capability_cache"):
            if key in source_bridge:
                bridge_summary[key] = source_bridge[key]
    bridge_summary["delivery"] = {
        "mode": "capability_compact",
        "full_result_source": "local_command_result",
        "full_result_hint": "Use command.status(include_result=true) or capability.search detail=full when full evidence is required.",
    }
    compact_payload["_bridge"] = bridge_summary

    compact = result.model_copy(deep=True)
    compact.result = compact_payload
    return compact


def prepare_result_delivery(
    transport,
    command: CommandEnvelope,
    result: ExecutionResult,
) -> ExecutionResult:
    ref = _v5_ref(transport, result.command_id)
    if ref is None:
        return result

    detail = str(command.arguments.get("detail") or "compact").strip().lower()
    if command.operation == "capability.search" and detail != "full":
        return _compact_capability_search(result)

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
    is_full_status_recovery = (
        command.adapter == "bridge_transport"
        and command.operation == "command.status"
        and bool(command.arguments.get("include_result"))
    )
    is_explicit_capability_full = command.operation == "capability.search" and detail == "full"
    if is_full_status_recovery or is_explicit_capability_full:
        if full_chars <= FULL_COMMENT_LIMIT_CHARS:
            return result
        publisher = getattr(transport, "_publish_json", None)
        if callable(publisher):
            full_result_path = _full_result_path(transport, ref, result.command_id)
            try:
                publisher(
                    full_result_path,
                    full_payload,
                    message=f"bridge on-demand full result {result.command_id}",
                    create_first=True,
                )
            except Exception:
                return result
            ref["command"] = _command_summary(command)
            return _full_reference_result(
                result,
                full_result_ref=full_result_path,
                full_result_chars=full_chars,
            )
        return result

    if full_chars <= COMPACT_THRESHOLD_CHARS:
        return result

    remember = getattr(transport, "remember_full_result", None)
    if callable(remember):
        remember(result)

    compact = result.model_copy(deep=True)
    compact.result = _structured_compact_payload(result.result, command_id=result.command_id)
    ref["command"] = _command_summary(command)
    return compact
