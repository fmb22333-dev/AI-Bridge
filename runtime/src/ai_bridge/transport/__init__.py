from __future__ import annotations

import json
import re

from .local_api import create_app
from . import github_bus as _github_bus
from ai_bridge.protocol.command import CommandEnvelope


CHANNEL_NACK_MARKER_V5 = "AI_BRIDGE_NACK_V5"
_github_bus.CHANNEL_NACK_MARKER_V5 = CHANNEL_NACK_MARKER_V5

_flat_command_keys = (
    "protocol",
    "command_id",
    "workspace",
    "adapter",
    "operation",
    "session",
    "project_file",
    "arguments",
    "execution",
    "risk",
)

_original_v5_comments_to_commands = _github_bus._v5_comments_to_commands
_original_v5_fetch_channel_commands = _github_bus._v5_fetch_channel_commands


def _normalize_v5_command(envelope: dict) -> dict:
    command = envelope.get("command")
    if isinstance(command, dict):
        return dict(command)
    return {key: envelope[key] for key in _flat_command_keys if key in envelope}


def _v5_patch_body(transport, comment_id: int, body: str, *, label: str) -> None:
    response = transport.client.patch(
        transport._comment_url(comment_id),
        headers=transport.headers,
        json={"body": body},
    )
    transport._observe_response(response)
    if response.status_code >= 400:
        raise RuntimeError(label + ": " + transport._response_detail(response))
    transport._comments_etag = None


def _v5_nack(transport, item: dict, envelope: dict, message: str) -> None:
    if str(envelope.get("bridge_id") or "") != transport.config.bridge_id:
        return
    channel_id = str(envelope.get("channel_id") or "").strip()
    generation = str(envelope.get("generation") or "").strip()
    if not channel_id or not generation:
        return
    try:
        comment_id = int(item["id"])
    except Exception:
        return
    command = _normalize_v5_command(envelope)
    command_id = str(command.get("command_id") or envelope.get("command_id") or "").strip() or None
    body = CHANNEL_NACK_MARKER_V5 + "\n" + json.dumps(
        {
            "bridge_id": transport.config.bridge_id,
            "channel_id": channel_id,
            "generation": generation,
            "command_id": command_id,
            "state": "rejected",
            "error": {"code": "INVALID_COMMAND_ENVELOPE", "message": message[:500]},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _v5_patch_body(transport, comment_id, body, label="Channel V5 NACK failed")


def _v5_deferred_channel_item(transport, item: dict, body: str):
    try:
        envelope = json.loads(body.split("\n", 1)[1])
        if not isinstance(envelope, dict):
            return None
    except Exception:
        return None

    if str(envelope.get("bridge_id") or "") != transport.config.bridge_id:
        return None
    channel_id = str(envelope.get("channel_id") or "").strip()
    generation = str(envelope.get("generation") or "").strip()
    try:
        if not channel_id or len(channel_id) > 128:
            raise ValueError("channel_id is required and must be <= 128 characters")
        if not generation or len(generation) > 160:
            raise ValueError("generation is required and must be <= 160 characters")
        command_payload = _normalize_v5_command(envelope)
        command = CommandEnvelope.model_validate(command_payload)
        comment_id = int(item["id"])
    except Exception as exc:
        _v5_nack(transport, item, envelope, f"{type(exc).__name__}: {exc}")
        return None

    transport._comment_refs[command.command_id] = {
        "comment_id": comment_id,
        "mode": "issue_channel_v5",
        "channel_id": channel_id,
        "generation": generation,
        "command": command_payload,
    }
    return command


def _v5_recover_ack_item(transport, item: dict, body: str):
    decoded = _github_bus._v5_decode_envelope(body, (_github_bus.CHANNEL_ACK_MARKER_V5,))
    if decoded is None:
        return None
    _, envelope = decoded
    if str(envelope.get("bridge_id") or "") != transport.config.bridge_id:
        return None
    channel_id = str(envelope.get("channel_id") or "").strip()
    generation = str(envelope.get("generation") or "").strip()
    command_payload = envelope.get("command")
    if not channel_id or not generation or not isinstance(command_payload, dict):
        return None
    try:
        command = CommandEnvelope.model_validate(command_payload)
        comment_id = int(item["id"])
    except Exception:
        return None
    if str(envelope.get("command_id") or command.command_id) != command.command_id:
        return None
    transport._comment_refs[command.command_id] = {
        "comment_id": comment_id,
        "mode": "issue_channel_v5",
        "channel_id": channel_id,
        "generation": generation,
        "command": command_payload,
    }
    return command


def _v5_reliable_comments_to_commands(transport, items: list[dict]):
    commands = []
    residual = []
    issue_suffix = f"/issues/{transport.config.issue_number}"
    for item in items:
        if not str(item.get("issue_url") or "").endswith(issue_suffix):
            residual.append(item)
            continue
        body = str(item.get("body") or "")
        if body.startswith(_github_bus.CHANNEL_COMMAND_MARKER_V5 + "\n"):
            command = _v5_deferred_channel_item(transport, item, body)
            if command is not None:
                commands.append(command)
            continue
        if body.startswith(_github_bus.CHANNEL_ACK_MARKER_V5 + "\n"):
            command = _v5_recover_ack_item(transport, item, body)
            if command is not None:
                commands.append(command)
            continue
        if body.startswith(CHANNEL_NACK_MARKER_V5 + "\n") or body.startswith(_github_bus.CHANNEL_RESULT_MARKER_V5 + "\n"):
            continue
        residual.append(item)
    commands.extend(_original_v5_comments_to_commands(transport, residual))
    deduped = []
    seen = set()
    for command in commands:
        if command.command_id in seen:
            continue
        seen.add(command.command_id)
        deduped.append(command)
    return deduped


def _v5_recent_issue_items(transport) -> list[dict]:
    headers = dict(transport.headers)
    params = {"per_page": 100}
    response = transport.client.get(transport._issue_comments_url(), headers=headers, params=params)
    transport._observe_response(response, poll=True)
    if response.status_code >= 400:
        if response.status_code in (401, 403, 404, 410):
            return []
        raise RuntimeError(transport._response_detail(response))
    items = response.json()
    items = items if isinstance(items, list) else []

    link = str(response.headers.get("Link") or "")
    match = re.search(r'<([^>]+)>;\s*rel="last"', link)
    if match:
        last = transport.client.get(match.group(1), headers=headers)
        transport._observe_response(last, poll=True)
        if last.status_code < 400:
            payload = last.json()
            if isinstance(payload, list):
                items.extend(payload)
    return items


def _v5_reliable_fetch_channel_commands(transport):
    used_cache = transport._cached_comment_items is not None
    if used_cache:
        items = transport._cached_comment_items
        transport._cached_comment_items = None
    else:
        response = transport._request_comment_page(conditional=True)
        transport._observe_response(response, poll=True)
        if response.status_code == 304:
            return []
        if response.status_code in (401, 403, 404, 410):
            transport._message_mode = "contents"
            transport._message_mode_detail = f"Dynamic channel discovery unavailable ({response.status_code}); Contents fallback active"
            return None
        if response.status_code >= 400:
            raise RuntimeError(transport._response_detail(response))
        transport._comments_etag = response.headers.get("ETag") or transport._comments_etag
        payload = response.json()
        items = payload if isinstance(payload, list) else []

    commands = _v5_reliable_comments_to_commands(transport, items)
    if not commands and len(items) >= 100:
        commands = _v5_reliable_comments_to_commands(transport, _v5_recent_issue_items(transport))

    if used_cache and not commands and transport._mailbox_comment_id is not None:
        legacy = _github_bus._p0_fetch_mailbox_commands(transport)
        if legacy is not None:
            return legacy
    return commands


def _v5_requires_durable_ack(transport, command) -> bool:
    ref = transport._comment_refs.get(command.command_id)
    return bool(ref and ref.get("mode") == "issue_channel_v5")


def _v5_ack_command(transport, command) -> None:
    ref = transport._comment_refs.get(command.command_id)
    if ref is None or ref.get("mode") != "issue_channel_v5":
        return
    comment_id = int(ref["comment_id"])
    response = transport.client.get(transport._comment_url(comment_id), headers=transport.headers)
    transport._observe_response(response)
    if response.status_code >= 400:
        raise RuntimeError("Channel V5 ACK ownership check failed: " + transport._response_detail(response))
    current = str((response.json() or {}).get("body") or "")
    decoded = _github_bus._v5_decode_envelope(
        current,
        (
            _github_bus.CHANNEL_COMMAND_MARKER_V5,
            _github_bus.CHANNEL_ACK_MARKER_V5,
            _github_bus.CHANNEL_RESULT_MARKER_V5,
            CHANNEL_NACK_MARKER_V5,
        ),
    )
    if decoded is None:
        raise RuntimeError("Channel V5 ACK ownership changed")
    marker, payload = decoded
    if (
        str(payload.get("bridge_id") or "") != transport.config.bridge_id
        or str(payload.get("channel_id") or "") != str(ref.get("channel_id") or "")
        or str(payload.get("generation") or "") != str(ref.get("generation") or "")
        or _github_bus._v5_command_id_from_payload(payload) != command.command_id
    ):
        raise RuntimeError("Channel V5 ACK ownership changed")
    if marker in {_github_bus.CHANNEL_ACK_MARKER_V5, _github_bus.CHANNEL_RESULT_MARKER_V5}:
        return
    if marker == CHANNEL_NACK_MARKER_V5:
        raise RuntimeError("Channel V5 command is already rejected")

    ack = _github_bus.CHANNEL_ACK_MARKER_V5 + "\n" + json.dumps(
        {
            "bridge_id": transport.config.bridge_id,
            "channel_id": ref["channel_id"],
            "generation": ref["generation"],
            "command_id": command.command_id,
            "command": ref["command"],
            "state": "accepted",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _v5_patch_body(transport, comment_id, ack, label="Channel V5 ACK failed")


_github_bus._v5_seen = lambda _transport, _key: False
_github_bus._v5_ack_channel_item = _v5_deferred_channel_item
_github_bus._v5_comments_to_commands = _v5_reliable_comments_to_commands
_github_bus._v5_fetch_channel_commands = _v5_reliable_fetch_channel_commands
_github_bus.GitHubBusTransport.requires_durable_ack = _v5_requires_durable_ack
_github_bus.GitHubBusTransport.ack_command = _v5_ack_command

__all__ = ["create_app"]
