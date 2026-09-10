import json

import httpx
import pytest

import ai_bridge.transport.github_bus as gb
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


def _command(command_id):
    return {
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    }


def _v5_body(channel_id, generation, command):
    return gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps({
        "bridge_id": "bridge-test",
        "channel_id": channel_id,
        "generation": generation,
        "command": command,
    })


def _item(cid, state):
    return {"id": cid, "issue_url": "https://api.github.com/repos/owner/repo/issues/1", "body": state[cid]}


def test_v5_discovers_two_independent_channels_in_one_comment_page():
    state = {
        100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}),
        201: _v5_body("chat-a", "gen-a", _command("cmd-a")),
        202: _v5_body("chat-b", "gen-b", _command("cmd-b")),
    }
    list_reads = 0
    def handler(request):
        nonlocal list_reads
        headers = {"ETag": '"v1"', "X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4900"}
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            list_reads += 1; return httpx.Response(200, headers=headers, json=[_item(cid, state) for cid in state])
        if request.method == "GET":
            cid = int(request.url.path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=_item(cid, state))
        if request.method == "PATCH":
            cid = int(request.url.path.rsplit("/", 1)[-1]); state[cid] = json.loads(request.content.decode())["body"]; return httpx.Response(200, headers=headers, json=_item(cid, state))
        raise AssertionError(f"unexpected {request.method} {request.url}")
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.initialize_message_mode() == "issue_channel_v5"
    commands = transport.fetch_commands()
    assert {c.command_id for c in commands} == {"cmd-a", "cmd-b"}
    assert state[201].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n")
    assert state[202].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n")
    for command in commands: transport.ack_command(command)
    assert state[201].startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n")
    assert state[202].startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n")
    assert list_reads == 1
    assert transport.message_state()["multi_channel"] is True


def test_v5_results_return_to_their_own_channel():
    state = {
        100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}),
        201: _v5_body("chat-a", "gen-a", _command("cmd-a")),
        202: _v5_body("chat-b", "gen-b", _command("cmd-b")),
    }
    def handler(request):
        headers = {"ETag": '"v1"'}
        if request.method == "GET" and request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, headers=headers, json=[_item(cid, state) for cid in state])
        if request.method == "GET":
            cid = int(request.url.path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=_item(cid, state))
        if request.method == "PATCH":
            cid = int(request.url.path.rsplit("/", 1)[-1]); state[cid] = json.loads(request.content.decode())["body"]; return httpx.Response(200, headers=headers, json=_item(cid, state))
        raise AssertionError
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); commands = transport.fetch_commands()
    for command in commands: transport.ack_command(command)
    transport.publish_result(ExecutionResult(command_id="cmd-a", status=ExecutionStatus.SUCCESS))
    transport.publish_result(ExecutionResult(command_id="cmd-b", status=ExecutionStatus.SUCCESS))
    assert state[201].startswith(gb.CHANNEL_RESULT_MARKER_V5 + "\n")
    assert state[202].startswith(gb.CHANNEL_RESULT_MARKER_V5 + "\n")
    assert json.loads(state[201].split("\n", 1)[1])["channel_id"] == "chat-a"
    assert json.loads(state[202].split("\n", 1)[1])["channel_id"] == "chat-b"


def test_v5_stale_result_never_overwrites_new_generation():
    state = {100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}), 201: _v5_body("chat-a", "gen-a", _command("cmd-a"))}
    result_patches = []
    def handler(request):
        headers = {"ETag": '"v1"'}
        if request.method == "GET" and request.url.path.endswith("/issues/comments"): return httpx.Response(200, headers=headers, json=[_item(cid, state) for cid in state])
        if request.method == "GET":
            cid = int(request.url.path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=_item(cid, state))
        if request.method == "PATCH":
            cid = int(request.url.path.rsplit("/", 1)[-1]); value = json.loads(request.content.decode())["body"];
            if value.startswith(gb.CHANNEL_RESULT_MARKER_V5 + "\n"): result_patches.append(value)
            state[cid] = value; return httpx.Response(200, headers=headers, json=_item(cid, state))
        raise AssertionError
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); commands = transport.fetch_commands(); transport.ack_command(commands[0])
    state[201] = _v5_body("chat-a", "gen-new", _command("cmd-new"))
    durable = []; transport._publish_json = lambda path, payload, **kwargs: durable.append((path, payload)) or "sha"
    transport.publish_result(ExecutionResult(command_id="cmd-a", status=ExecutionStatus.SUCCESS))
    assert result_patches == []; assert state[201].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n"); assert durable
    assert "/channels/chat-a/cmd-a.json" in durable[0][0]; assert durable[0][1]["fallback_reason"] == "channel_generation_changed"


def test_v5_reuses_v4_mailbox_even_when_it_is_not_idle():
    v4 = gb.RESULT_MARKER_V4 + "\n" + json.dumps({"bridge_id": "bridge-test", "generation": "old-gen", "command_id": "old-command", "command": _command("old-command"), "result": {"command_id": "old-command", "status": "success"}})
    created = []
    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/issues/comments"): return httpx.Response(200, json=[{"id": 321, "issue_url": "https://api.github.com/repos/owner/repo/issues/1", "body": v4}])
        if request.method == "POST": created.append(True); return httpx.Response(201, json={"id": 999, "body": ""})
        raise AssertionError
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.initialize_message_mode() == "issue_channel_v5"; assert transport.message_state()["mailbox_comment_id"] == 321; assert created == []


def test_v5_retries_command_after_transient_ack_patch_failure():
    state = {100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}), 201: _v5_body("chat-a", "gen-a", _command("cmd-a"))}
    ack_attempts = 0
    def handler(request):
        nonlocal ack_attempts
        headers = {"ETag": '"v1"'}
        if request.method == "GET" and request.url.path.endswith("/issues/comments"): return httpx.Response(200, headers=headers, json=[_item(cid, state) for cid in state])
        if request.method == "GET":
            cid = int(request.url.path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=_item(cid, state))
        if request.method == "PATCH":
            cid = int(request.url.path.rsplit("/", 1)[-1]); value = json.loads(request.content.decode())["body"]
            if value.startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n"):
                ack_attempts += 1
                if ack_attempts == 1: return httpx.Response(500, headers=headers, json={"message": "transient"})
            state[cid] = value; return httpx.Response(200, headers=headers, json=_item(cid, state))
        raise AssertionError(f"unexpected {request.method} {request.url}")
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); commands = transport.fetch_commands(); assert [c.command_id for c in commands] == ["cmd-a"]
    with pytest.raises(RuntimeError, match="Channel V5 ACK failed"): transport.ack_command(commands[0])
    transport.ack_command(commands[0])
    assert ack_attempts == 2; assert state[201].startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n")


def test_v5_accepts_flat_command_envelope_for_sender_compatibility():
    command = _command("cmd-flat"); flat = {"bridge_id": "bridge-test", "channel_id": "chat-flat", "generation": "gen-flat", **command}
    state = {100: gb.MAILBOX_MARKER_V3 + "\n" + json.dumps({"bridge_id": "bridge-test", "state": "idle"}), 201: gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps(flat)}
    def handler(request):
        headers = {"ETag": '"v1"'}
        if request.method == "GET" and request.url.path.endswith("/issues/comments"): return httpx.Response(200, headers=headers, json=[_item(cid, state) for cid in state])
        if request.method == "GET":
            cid = int(request.url.path.rsplit("/", 1)[-1]); return httpx.Response(200, headers=headers, json=_item(cid, state))
        if request.method == "PATCH":
            cid = int(request.url.path.rsplit("/", 1)[-1]); state[cid] = json.loads(request.content.decode())["body"]; return httpx.Response(200, headers=headers, json=_item(cid, state))
        raise AssertionError(f"unexpected {request.method} {request.url}")
    transport = gb.GitHubBusTransport(gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); commands = transport.fetch_commands(); assert [item.command_id for item in commands] == ["cmd-flat"]
    assert state[201].startswith(gb.CHANNEL_COMMAND_MARKER_V5 + "\n"); transport.ack_command(commands[0]); assert state[201].startswith(gb.CHANNEL_ACK_MARKER_V5 + "\n")
