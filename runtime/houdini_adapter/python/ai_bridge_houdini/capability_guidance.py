from __future__ import annotations

import copy
import json
import time
from collections import defaultdict, deque
from pathlib import Path


GUIDANCE_PATH = Path(__file__).resolve().parent / "knowledge" / "capability_guidance.json"

_last_good = None
_last_error = None
_recent = defaultdict(deque)
_last_emit = {}


def _load():
    global _last_good, _last_error
    try:
        data = json.loads(GUIDANCE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("capability guidance must be an object")
        policy = data.get("policy")
        entries = data.get("entries")
        if not isinstance(policy, dict):
            raise ValueError("capability guidance policy must be an object")
        if not isinstance(entries, list):
            raise ValueError("capability guidance entries must be a list")
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"entries[{index}] must be an object")
            if not str(item.get("id") or "").strip():
                raise ValueError(f"entries[{index}] requires id")
            if not str(item.get("capability") or "").strip():
                raise ValueError(f"entries[{index}] requires capability")
            if item.get("state") not in {"candidate", "validated", "promoted", "deprecated"}:
                raise ValueError(f"entries[{index}] has invalid state")
        _last_good = copy.deepcopy(data)
        _last_error = None
        return data
    except Exception as exc:
        _last_error = {"code": "CAPABILITY_GUIDANCE_VALIDATION_FAILED", "message": str(exc)}
        if _last_good is not None:
            return copy.deepcopy(_last_good)
        return {
            "schema_version": "1.0",
            "policy": {"mode": "disabled", "blocking": False, "auto_execute": False},
            "entries": [],
        }


def catalog(*, promoted_only: bool = False) -> dict:
    data = _load()
    entries = list(data.get("entries") or [])
    if promoted_only:
        entries = [item for item in entries if item.get("state") == "promoted"]
    return {
        "policy": copy.deepcopy(data.get("policy") or {}),
        "entries": copy.deepcopy(entries),
    }


def status() -> dict:
    data = _load()
    return {
        "entry_count": len(data.get("entries") or []),
        "promoted_count": sum(1 for item in data.get("entries") or [] if item.get("state") == "promoted"),
        "candidate_count": sum(1 for item in data.get("entries") or [] if item.get("state") == "candidate"),
        "degraded": _last_error is not None,
        "validation_error": copy.deepcopy(_last_error),
        "hot_reload": True,
        "blocking": False,
        "auto_execute": False,
    }


def enrich_capabilities(capabilities: list[dict]) -> list[dict]:
    promoted = {
        item["capability"]: item
        for item in catalog(promoted_only=True)["entries"]
    }
    out = []
    for capability in capabilities or []:
        row = copy.deepcopy(capability)
        meta = promoted.get(row.get("name"))
        if meta:
            row["guidance"] = {
                "state": meta["state"],
                "kind": meta.get("kind"),
                "scope": meta.get("scope"),
                "preferred_for": list(meta.get("preferred_for") or []),
                "supersedes": list(meta.get("supersedes") or []),
                "not_for": list(meta.get("not_for") or []),
                "advisory_only": True,
            }
        out.append(row)
    return out


def _match_sequence(history: list[str], sequence: list[str]) -> bool:
    if not sequence or len(history) < len(sequence):
        return False
    return history[-len(sequence):] == sequence


def _match_repeat(history: list[str], spec: dict) -> bool:
    operation = str(spec.get("operation") or "")
    try:
        count = max(2, int(spec.get("count", 2)))
    except Exception:
        count = 2
    if not operation or len(history) < count:
        return False
    return history[-count:] == [operation] * count


def observe(session_id: str, operation: str, *, now: float | None = None) -> list[dict]:
    if not session_id or not operation:
        return []

    data = _load()
    policy = data.get("policy") or {}
    if policy.get("mode") != "advisory_only":
        return []

    current = time.monotonic() if now is None else float(now)
    try:
        max_ops = max(2, min(int(policy.get("observation_window_operations", 8)), 64))
    except Exception:
        max_ops = 8
    try:
        max_age = max(1.0, float(policy.get("observation_window_seconds", 120)))
    except Exception:
        max_age = 120.0
    try:
        cooldown = max(0.0, float(policy.get("suggestion_cooldown_seconds", 60)))
    except Exception:
        cooldown = 60.0
    try:
        limit = max(1, min(int(policy.get("max_suggestions_per_result", 2)), 8))
    except Exception:
        limit = 2

    bucket = _recent[str(session_id)]
    bucket.append((current, str(operation)))
    while bucket and (len(bucket) > max_ops or current - bucket[0][0] > max_age):
        bucket.popleft()

    history = [item[1] for item in bucket]
    suggestions = []

    for entry in data.get("entries") or []:
        if entry.get("state") != "promoted":
            continue
        capability = str(entry.get("capability") or "")
        if capability == operation:
            continue

        trigger = entry.get("triggers") or {}
        matched = False
        for sequence in trigger.get("sequences") or []:
            if isinstance(sequence, list) and _match_sequence(history, [str(x) for x in sequence]):
                matched = True
                break
        if not matched and isinstance(trigger.get("repeat_operation"), dict):
            matched = _match_repeat(history, trigger["repeat_operation"])
        if not matched:
            continue

        key = (str(session_id), str(entry.get("id")))
        last = _last_emit.get(key)
        if last is not None and current - last < cooldown:
            continue
        _last_emit[key] = current

        suggestions.append({
            "id": entry.get("id"),
            "capability": capability,
            "kind": entry.get("kind"),
            "scope": entry.get("scope"),
            "message": entry.get("suggestion"),
            "advisory_only": True,
            "blocking": False,
            "auto_execute": False,
            "preferred_for": list(entry.get("preferred_for") or []),
            "not_for": list(entry.get("not_for") or []),
        })
        if len(suggestions) >= limit:
            break

    return suggestions


def _reset_runtime_state_for_tests():
    _recent.clear()
    _last_emit.clear()
