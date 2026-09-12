from __future__ import annotations

import json
import re

from pydantic import ValidationError

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
_original_message_state = _github_bus.GitHubBusTransport.message_state
_original_fetch_contents_commands = _github_bus.GitHubBusTransport._fetch_contents_commands


def _normalize_v5_command(envelope: dict) -> dict:
    command = envelope.get("command")
    if isinstance(command, dict):
        return dict(command)
    return {key: envelope[key] for key in _flat_command_keys if key in envelope}


def _command_signature(command: CommandEnvelope) -> str:
    return json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_commands(*groups) -> list[CommandEnvelope]:
    ordered: list[CommandEnvelope] = []
    signatures: dict[str, str] = {}
    for group in groups:
        for command in group or []:
            signature = _command_signature(command)
            prior = signatures.get(command.command_id)
            if prior is None:
                signatures[command.command_id] = signature
                ordered.append(command)
                continue
            if prior != signature:
                raise RuntimeError(
                    f"COMMAND_IDENTITY_CONFLICT: {command.command_id} arrived with divergent payloads"
                )
    return ordered


def _receipt_store(transport) -> dict[str, list[dict]]:
    store = getattr(transport, "_v52_command_receipts", None)
    if store is None:
        store = {}
        transport._v52_command_receipts = store
    return store


def _record_receipt(
    transport,
    command: CommandEnvelope,
    *,
    receipt_id: str,
    ingress_kind: str,
    routing: dict | None = None,
) -> None:
    store = _receipt_store(transport)
    items = store.setdefault(command.command_id, [])
    if any(str(item.get("receipt_id")) == receipt_id for item in items):
        return
    items.append(
        {
            "receipt_id": receipt_id,
            "ingress_kind": ingress_kind,
            "routing": dict(routing or {}),
        }
    )


def _command_receipts(transport, command_id: str) -> list[dict]:
    return [dict(item) for item in _receipt_store(transport).get(command_id, [])]


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

    ref = {
        "comment_id": comment_id,
        "mode": "issue_channel_v5",
        "channel_id": channel_id,
        "generation": generation,
        "command": command_payload,
    }
    transport._comment_refs.setdefault(command.command_id, ref)
    _record_receipt(
        transport,
        command,
        receipt_id=f"issue:{comment_id}:{channel_id}:{generation}",
        ingress_kind="issue_comment",
        routing={"ref": ref},
    )
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
    ref = {
        "comment_id": comment_id,
        "mode": "issue_channel_v5",
        "channel_id": channel_id,
        "generation": generation,
        "command": command_payload,
    }
    transport._comment_refs.setdefault(command.command_id, ref)
    _record_receipt(
        transport,
        command,
        receipt_id=f"issue:{comment_id}:{channel_id}:{generation}",
        ingress_kind="issue_comment",
        routing={"ref": ref},
    )
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
    return _merge_commands(commands)


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


def _restore_contents_command_index(transport, index) -> None:
    if index is None:
        transport._v53_contents_index_restored = False
        return
    transport._known_command_shas = {str(name): str(sha) for name, sha in index.items()}
    transport._command_index_initialized = True
    transport._v53_contents_index_restored = True


def _contents_command_index_snapshot(transport):
    if not transport._command_index_initialized:
        return None
    return dict(transport._known_command_shas)


def _contents_commands_with_receipts(transport) -> list[CommandEnvelope]:
    hybrid_bootstrap = (
        str(getattr(transport, "_message_mode", "")) == "issue_channel_v5"
        and not bool(getattr(transport, "_command_index_initialized", False))
        and not bool(getattr(transport, "_v53_contents_index_restored", False))
    )
    try:
        commands = _original_fetch_contents_commands(transport)
        if hybrid_bootstrap:
            commands = []
            transport._v52_contents_ingress_detail = "available: baseline_seeded"
        else:
            transport._v52_contents_ingress_detail = "available"
    except ValidationError:
        transport._v52_contents_ingress_detail = "invalid_command"
        raise
    except Exception as exc:
        transport._v52_contents_ingress_detail = f"unavailable: {type(exc).__name__}: {exc}"
        return []
    for command in commands:
        _record_receipt(
            transport,
            command,
            receipt_id=f"contents:{command.command_id}",
            ingress_kind="contents",
            routing={
                "result_path": transport._bus_path(
                    f"results/{transport.config.bridge_id}/{command.command_id}.json"
                )
            },
        )
    return commands


def _v5_reliable_fetch_channel_commands(transport):
    used_cache = transport._cached_comment_items is not None
    items: list[dict] = []
    comment_commands: list[CommandEnvelope] = []

    if used_cache:
        items = transport._cached_comment_items
        transport._cached_comment_items = None
    else:
        try:
            response = transport._request_comment_page(conditional=True)
            transport._observe_response(response, poll=True)
        except Exception as exc:
            transport._v52_comment_ingress_detail = f"unavailable: {type(exc).__name__}: {exc}"
            response = None

        if response is not None:
            if response.status_code == 304:
                transport._v52_comment_ingress_detail = "available: not_modified"
            elif response.status_code in (401, 403, 404, 410):
                transport._message_mode = "contents"
                transport._message_mode_detail = f"Dynamic channel discovery unavailable ({response.status_code}); Contents fallback active"
                return None
            elif response.status_code >= 400:
                transport._v52_comment_ingress_detail = (
                    f"unavailable: {transport._response_detail(response)}"
                )
            else:
                transport._v52_comment_ingress_detail = "available"
                transport._comments_etag = response.headers.get("ETag") or transport._comments_etag
                payload = response.json()
                items = payload if isinstance(payload, list) else []

    if items:
        comment_commands = _v5_reliable_comments_to_commands(transport, items)
        if not comment_commands and len(items) >= 100:
            comment_commands = _v5_reliable_comments_to_commands(
                transport, _v5_recent_issue_items(transport)
            )

    if used_cache and not comment_commands and transport._mailbox_comment_id is not None:
        legacy = _github_bus._p0_fetch_mailbox_commands(transport)
        if legacy:
            comment_commands = legacy

    contents_commands = _contents_commands_with_receipts(transport)
    return _merge_commands(comment_commands, contents_commands)


def _v5_requires_durable_ack(transport, command) -> bool:
    return bool(
        transport._comment_refs.get(command.command_id)
        or _command_receipts(transport, command.command_id)
    )


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


def _publish_result_for_receipt(transport, result, receipt: dict) -> None:
    kind = str(receipt.get("ingress_kind") or "")
    routing = receipt.get("routing") if isinstance(receipt.get("routing"), dict) else {}
    if kind == "issue_comment":
        ref = routing.get("ref") if isinstance(routing.get("ref"), dict) else None
        if ref is None:
            raise RuntimeError("ISSUE_RECEIPT_REF_MISSING")
        prior = transport._comment_refs.get(result.command_id)
        transport._comment_refs[result.command_id] = dict(ref)
        try:
            transport.publish_result(result)
        finally:
            if prior is None:
                transport._comment_refs.pop(result.command_id, None)
            else:
                transport._comment_refs[result.command_id] = prior
        return
    if kind == "contents":
        transport._publish_json(
            transport._bus_path(
                f"results/{transport.config.bridge_id}/{result.command_id}.json"
            ),
            json.loads(result.model_dump_json()),
            message=f"bridge result {result.command_id}",
            create_first=True,
        )
        return
    raise RuntimeError(f"UNKNOWN_INGRESS_RECEIPT_KIND: {kind}")


def _v52_message_state(transport) -> dict:
    state = _original_message_state(transport)
    mode = str(transport._message_mode or state.get("mode") or "contents")
    if mode == "issue_channel_v5":
        state["multi_ingress"] = True
        state["primary_ingress"] = "issue_comment_v5"
        state["fallback_ingress"] = "contents"
        state["comment_ingress_detail"] = getattr(
            transport, "_v52_comment_ingress_detail", "available"
        )
        state["contents_ingress_detail"] = getattr(
            transport, "_v52_contents_ingress_detail", "not_polled"
        )
    elif mode == "contents":
        state["multi_ingress"] = False
        state["active_ingress"] = "contents"
        state["fallback_ingress"] = "contents"
        state["comment_ingress_detail"] = getattr(
            transport, "_v52_comment_ingress_detail", "inactive"
        )
        state["contents_ingress_detail"] = getattr(
            transport, "_v52_contents_ingress_detail", "available"
        )
    else:
        state["multi_ingress"] = False
        state["active_ingress"] = mode
    return state


_github_bus._v5_seen = lambda _transport, _key: False
_github_bus._v5_ack_channel_item = _v5_deferred_channel_item
_github_bus._v5_comments_to_commands = _v5_reliable_comments_to_commands
_github_bus._v5_fetch_channel_commands = _v5_reliable_fetch_channel_commands
_github_bus.GitHubBusTransport._fetch_contents_commands = _contents_commands_with_receipts
_github_bus.GitHubBusTransport.restore_contents_command_index = _restore_contents_command_index
_github_bus.GitHubBusTransport.contents_command_index_snapshot = _contents_command_index_snapshot
_github_bus.GitHubBusTransport.requires_durable_ack = _v5_requires_durable_ack
_github_bus.GitHubBusTransport.ack_command = _v5_ack_command
_github_bus.GitHubBusTransport.command_receipts = _command_receipts
_github_bus.GitHubBusTransport.publish_result_for_receipt = _publish_result_for_receipt
_github_bus.GitHubBusTransport.message_state = _v52_message_state

__all__ = ["create_app"]