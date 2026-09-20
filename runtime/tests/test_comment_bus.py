import json

import httpx

from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.github_bus import (
    COMMAND_MARKER,
    COMMAND_MARKER_V3,
    MAILBOX_MARKER_V3,
    RESULT_MARKER,
    RESULT_MARKER_V3,
    GitHubBusConfig,
    GitHubBusTransport,
)


def _command(command_id: str) -> dict:
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Houdini Test",
        "adapter": "houdini",
        "operation": "inspect.context",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "auto", "dry_run": False},
        "risk": "L1",
    }


def test_mailbox_v3_discovers_fixed_comment_polls_specific_comment_and_patches_in_place():
    command = _command("cmd-mailbox-fast")
    mailbox_idle = MAILBOX_MARKER_V3 + "\n" + json.dumps(
        {"bridge_id": "bridge-test", "protocol": "bridge/1", "state": "idle"}
    )
    command_body = COMMAND_MARKER_V3 + "\n" + json.dumps(
        {"bridge_id": "bridge-test", "command": command}
    )
    calls = []
    specific_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal specific_reads
        calls.append((request.method, request.url.path, dict(request.headers)))
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4990",
            "X-RateLimit-Reset": "2000000000",
        }
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            headers["ETag"] = '"comments-v1"'
            return httpx.Response(
                200,
                headers=headers,
                json=[{
                    "id": 321,
                    "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
                    "body": mailbox_idle,
                }],
            )
        if request.method == "GET" and request.url.path.endswith("/issues/comments/321"):
            specific_reads += 1
            headers["ETag"] = '"mailbox-command"'
            return httpx.Response(
                200,
                headers=headers,
                json={"id": 321, "body": command_body},
            )
        if request.method == "PATCH" and request.url.path.endswith("/issues/comments/321"):
            body = json.loads(request.content.decode("utf-8"))["body"]
            assert body.startswith(RESULT_MARKER_V3 + "\n")
            payload = json.loads(body.split("\n", 1)[1])
            assert payload["command_id"] == "cmd-mailbox-fast"
            headers["ETag"] = '"mailbox-result"'
            return httpx.Response(200, headers=headers, json={"id": 321, "body": body})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert transport.initialize_message_mode() == "issue_channel_v5"
    assert transport.message_state()["mailbox_comment_id"] == 321
    assert [c.command_id for c in transport.fetch_commands()] == ["cmd-mailbox-fast"]

    transport.publish_result(
        ExecutionResult(command_id="cmd-mailbox-fast", status=ExecutionStatus.SUCCESS)
    )

    assert transport.message_state()["comment_write_ok"] is True
    assert specific_reads == 1
    assert not any(method == "PUT" for method, _, _ in calls)


def test_mailbox_v3_creates_mailbox_when_missing():
    created_body = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_body
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path.endswith("/issues/1/comments"):
            created_body = json.loads(request.content.decode("utf-8"))["body"]
            return httpx.Response(201, json={"id": 654, "body": created_body})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert transport.initialize_message_mode() == "issue_channel_v5"
    assert transport.message_state()["mailbox_comment_id"] == 654
    assert created_body.startswith(MAILBOX_MARKER_V3 + "\n")


def test_issue_comment_v2_remains_fallback_when_mailbox_creation_unavailable():
    command = _command("cmd-comment-fast")
    comment_body = COMMAND_MARKER + "\n" + json.dumps(
        {"bridge_id": "bridge-test", "command": command}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4990",
            "X-RateLimit-Reset": "2000000000",
        }
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            headers["ETag"] = '"comments-v1"'
            return httpx.Response(
                200,
                headers=headers,
                json=[{
                    "id": 12345,
                    "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
                    "body": comment_body,
                }],
            )
        if request.method == "POST" and request.url.path.endswith("/issues/1/comments"):
            return httpx.Response(403, headers=headers, json={"message": "forbidden"})
        if request.method == "PATCH" and request.url.path.endswith("/issues/comments/12345"):
            body = json.loads(request.content.decode("utf-8"))["body"]
            assert body.startswith(RESULT_MARKER + "\n")
            return httpx.Response(200, headers=headers, json={"id": 12345, "body": body})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert transport.initialize_message_mode() == "issue_comment_v2"
    assert [c.command_id for c in transport.fetch_commands()] == ["cmd-comment-fast"]
    transport.publish_result(
        ExecutionResult(command_id="cmd-comment-fast", status=ExecutionStatus.SUCCESS)
    )
    assert transport.message_state()["comment_write_ok"] is True


def test_issue_api_unavailable_falls_back_to_contents():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(
                403,
                json={"message": "Resource not accessible by personal access token"},
            )
        if request.url.path.endswith("/contents/.ai-bridge/commands/bridge-test"):
            return httpx.Response(404)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert transport.initialize_message_mode() == "contents"
    assert transport.fetch_commands() == []
    assert transport.message_state()["mode"] == "contents"
