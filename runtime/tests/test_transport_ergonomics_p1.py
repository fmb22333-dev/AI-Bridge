from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

import ai_bridge.transport  # install reliable V5 transport wrappers
import ai_bridge.transport.github_bus as gb
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.result_delivery import prepare_result_delivery


class FakeV5Transport:
    def __init__(self):
        self.config = SimpleNamespace(bridge_id="bridge-test")
        self._comment_refs = {
            "cmd-large": {
                "mode": "issue_channel_v5",
                "channel_id": "chat-p1",
                "generation": "gen-p1",
                "command": {"old": "full"},
            }
        }
        self.contents_writes = []
        self.remembered = []

    def _bus_path(self, path):
        return ".ai-bridge/" + path

    def _publish_json(self, path, payload, **kwargs):
        self.contents_writes.append((path, payload, kwargs))
        return "sha"

    def remember_full_result(self, result):
        self.remembered.append(result.model_copy(deep=True))


def _command(command_id="cmd-large"):
    return CommandEnvelope(
        command_id=command_id,
        workspace="Bridge",
        adapter="bridge_admin",
        operation="bridge.update.status",
        arguments={},
        execution={"verify": True, "checkpoint": "none", "dry_run": False},
        risk="L1",
    )


def _large_result():
    return ExecutionResult(
        command_id="cmd-large",
        status=ExecutionStatus.SUCCESS,
        result={
            "diagnostic": {
                "status": "FAILED",
                "code": "COOK_ERROR",
                "message": "m" * 9000,
                "samples": [
                    {"frame": index, "value": index * 0.1, "label": "sample-" + str(index)}
                    for index in range(500)
                ],
            },
            "count": 500,
            "digest": "abc123",
        },
    )


def test_large_nested_result_preserves_structure_without_contents_archive():
    transport = FakeV5Transport()
    original = _large_result()
    delivered = prepare_result_delivery(transport, _command(), original)
    assert transport.contents_writes == []
    assert transport.remembered
    assert transport.remembered[-1].result["diagnostic"]["message"] == "m" * 9000
    assert delivered.result["diagnostic"]["status"] == "FAILED"
    assert delivered.result["diagnostic"]["code"] == "COOK_ERROR"
    assert delivered.result["count"] == 500
    assert delivered.result["digest"] == "abc123"
    delivery = delivered.result["_bridge"]["delivery"]
    assert delivery["mode"] == "structured_summary"
    assert delivery["full_result_source"] == "local_command_result"
    assert delivery["recovery"] == {
        "adapter": "bridge_transport",
        "operation": "command.status",
        "arguments": {"command_id": "cmd-large", "include_result": True},
    }
    paths = delivery["truncated_paths"] + delivery["omitted_paths"]
    assert "$.diagnostic.message" in paths
    assert any(path.startswith("$.diagnostic.samples") for path in paths)


def test_small_result_remains_fast_path_and_is_not_remembered():
    transport = FakeV5Transport()
    original = ExecutionResult(command_id="cmd-large", status=ExecutionStatus.SUCCESS, result={"ok": True, "value": 42})
    delivered = prepare_result_delivery(transport, _command(), original)
    assert delivered is original
    assert transport.contents_writes == []
    assert transport.remembered == []


def test_structured_summary_is_bounded_for_large_lists_and_strings():
    transport = FakeV5Transport()
    delivered = prepare_result_delivery(transport, _command(), _large_result())
    diagnostic = delivered.result["diagnostic"]
    assert isinstance(diagnostic["message"], dict)
    assert diagnostic["message"]["_truncated"] is True
    assert diagnostic["message"]["chars"] == 9000
    assert diagnostic["message"]["prefix"]
    assert diagnostic["message"]["suffix"]
    samples = diagnostic["samples"]
    assert isinstance(samples, dict)
    assert samples["_summary"] == "list"
    assert samples["count"] == 500
    assert 1 <= len(samples["head"]) <= 8
    assert len(samples["tail"]) <= 3


def _real_transport(*, current_generation="gen-p1", patch_status=200):
    command = _command().model_dump(mode="json")
    state = {
        "body": gb.CHANNEL_ACK_MARKER_V5 + "\n" + json.dumps({
            "bridge_id": "bridge-test",
            "channel_id": "chat-p1",
            "generation": current_generation,
            "command_id": "cmd-large",
            "command": command,
            "state": "accepted",
        })
    }

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/issues/comments/201"):
            return httpx.Response(200, json={"id": 201, "body": state["body"]})
        if request.method == "PATCH" and request.url.path.endswith("/issues/comments/201"):
            if patch_status >= 400:
                return httpx.Response(patch_status, json={"message": "patch failed"})
            state["body"] = json.loads(request.content.decode())["body"]
            return httpx.Response(200, json={"id": 201, "body": state["body"]})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = gb.GitHubBusTransport(
        gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transport._message_mode = "issue_channel_v5"
    transport._comment_refs["cmd-large"] = {
        "comment_id": 201,
        "mode": "issue_channel_v5",
        "channel_id": "chat-p1",
        "generation": "gen-p1",
        "command": command,
    }
    durable = []
    transport._publish_json = lambda path, payload, **kwargs: durable.append((path, payload, kwargs)) or "sha"
    return transport, durable


def test_stale_generation_fallback_archives_original_full_result():
    transport, durable = _real_transport(current_generation="gen-new")
    original = _large_result()
    compact = prepare_result_delivery(transport, _command(), original)
    transport.publish_result(compact)
    assert durable
    archived = durable[-1][1]
    assert archived["fallback_reason"] == "channel_generation_changed"
    assert archived["result"]["result"]["diagnostic"]["message"] == "m" * 9000


def test_comment_patch_failure_archives_original_full_result():
    transport, durable = _real_transport(current_generation="gen-p1", patch_status=500)
    original = _large_result()
    compact = prepare_result_delivery(transport, _command(), original)
    transport.publish_result(compact)
    assert durable
    archived = durable[-1][1]
    assert archived["fallback_reason"] == "comment_patch_http_500"
    assert archived["result"]["result"]["diagnostic"]["message"] == "m" * 9000
