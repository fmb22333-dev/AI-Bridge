import base64
import json

import httpx
import pytest

import ai_bridge.transport.github_bus as gb
from ai_bridge.persistence.db import BridgeDB, CommandIdentityConflict
from ai_bridge.protocol.command import CommandEnvelope


def _command(command_id="cmd-multi", *, operation="bridge.update.status", arguments=None):
    return CommandEnvelope.model_validate(
        {
            "protocol": "bridge/1",
            "command_id": command_id,
            "workspace": "Bridge",
            "adapter": "bridge_admin",
            "operation": operation,
            "arguments": arguments or {},
            "execution": {
                "verify": True,
                "checkpoint": "none",
                "dry_run": False,
            },
            "risk": "L1",
        }
    )


def test_transport_command_claim_has_exactly_one_winner(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    command = _command()

    first = db.accept_transport_command(command)
    second = db.accept_transport_command(command)

    assert first["status"] == "transport_accepted"
    assert second["status"] == "transport_accepted"
    assert db.claim_transport_command(command) == "claimed"
    assert db.claim_transport_command(command) == "already_claimed"
    assert db.get_command(command.command_id)["status"] == "received"


def test_same_command_id_with_divergent_payload_fails_closed(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    db.accept_transport_command(_command())

    with pytest.raises(CommandIdentityConflict):
        db.accept_transport_command(
            _command(operation="bridge.project.resume", arguments={"project": "auto_uv"})
        )


def test_terminal_transport_command_reports_terminal_claim(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    command = _command("cmd-terminal")
    db.accept_transport_command(command)
    assert db.claim_transport_command(command) == "claimed"
    with db._connect() as conn:
        conn.execute(
            "UPDATE commands SET status='success', result_json=? WHERE command_id=?",
            (
                json.dumps(
                    {
                        "command_id": command.command_id,
                        "status": "success",
                        "result": {},
                    }
                ),
                command.command_id,
            ),
        )

    assert db.claim_transport_command(command) == "terminal"


def test_multiple_ingress_receipts_do_not_overwrite_each_other(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    command = _command("cmd-receipts")
    db.accept_transport_command(command)

    db.record_ingress_receipt(
        "github:owner/repo:main:bridge-test",
        command.command_id,
        "issue:201:chat-a:gen-a",
        "issue_comment",
        {"comment_id": 201, "channel_id": "chat-a", "generation": "gen-a"},
    )
    db.record_ingress_receipt(
        "github:owner/repo:main:bridge-test",
        command.command_id,
        "contents:cmd-receipts.json",
        "contents",
        {"name": "cmd-receipts.json"},
    )

    receipts = db.list_ingress_receipts(
        "github:owner/repo:main:bridge-test", command.command_id
    )
    assert [item["ingress_kind"] for item in receipts] == ["contents", "issue_comment"]
    assert receipts[0]["routing"]["name"] == "cmd-receipts.json"
    assert receipts[1]["routing"]["comment_id"] == 201


def _v5_body(command, channel_id="chat-a", generation="gen-a"):
    return gb.CHANNEL_COMMAND_MARKER_V5 + "\n" + json.dumps(
        {
            "bridge_id": "bridge-test",
            "channel_id": channel_id,
            "generation": generation,
            "command": command.model_dump(mode="json"),
        }
    )


def _hybrid_transport(comment_commands, content_commands):
    mailbox = gb.MAILBOX_MARKER_V3 + "\n" + json.dumps(
        {"bridge_id": "bridge-test", "state": "idle"}
    )
    comments = [
        {
            "id": 100,
            "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
            "body": mailbox,
        }
    ]
    for offset, command in enumerate(comment_commands, start=201):
        comments.append(
            {
                "id": offset,
                "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
                "body": _v5_body(command, f"chat-{offset}", f"gen-{offset}"),
            }
        )

    content_payloads = {
        f"{command.command_id}.json": json.dumps(command.model_dump(mode="json")).encode()
        for command in content_commands
    }

    def handler(request):
        path = request.url.path
        headers = {"ETag": '"hybrid-v1"'}
        if request.method == "GET" and path.endswith("/issues/comments"):
            return httpx.Response(200, headers=headers, json=comments)
        if request.method == "GET" and path.endswith(
            "/contents/.ai-bridge/commands/bridge-test"
        ):
            return httpx.Response(
                200,
                headers=headers,
                json=[
                    {
                        "type": "file",
                        "name": name,
                        "sha": f"sha-{index}",
                    }
                    for index, name in enumerate(sorted(content_payloads), start=1)
                ],
            )
        if request.method == "GET" and path.endswith(
            "/contents/.ai-bridge/results/bridge-test"
        ):
            return httpx.Response(404, headers=headers, json={"message": "Not Found"})
        if request.method == "GET" and "/contents/.ai-bridge/commands/bridge-test/" in path:
            name = path.rsplit("/", 1)[-1]
            raw = content_payloads[name]
            return httpx.Response(
                200,
                headers=headers,
                json={"content": base64.b64encode(raw).decode("ascii")},
            )
        if request.method == "GET" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1])
            item = next(value for value in comments if value["id"] == cid)
            return httpx.Response(200, headers=headers, json=item)
        if request.method == "PATCH" and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1])
            item = next(value for value in comments if value["id"] == cid)
            item["body"] = json.loads(request.content.decode())["body"]
            return httpx.Response(200, headers=headers, json=item)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return gb.GitHubBusTransport(
        gb.GitHubBusConfig(
            repository="owner/repo",
            token="x",
            bridge_id="bridge-test",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_v5_healthy_comment_mode_also_polls_contents_and_merges_same_command():
    shared = _command("cmd-shared")
    contents_only = _command("cmd-contents")
    transport = _hybrid_transport([shared], [shared, contents_only])

    assert transport.initialize_message_mode() == "issue_channel_v5"
    commands = transport.fetch_commands()

    assert [item.command_id for item in commands] == ["cmd-shared", "cmd-contents"]
    shared_receipts = transport.command_receipts("cmd-shared")
    assert [item["ingress_kind"] for item in shared_receipts] == [
        "issue_comment",
        "contents",
    ]
    assert transport.command_receipts("cmd-contents")[0]["ingress_kind"] == "contents"
    state = transport.message_state()
    assert state["multi_ingress"] is True
    assert state["fallback_ingress"] == "contents"


def test_v5_same_command_id_with_divergent_comment_and_contents_payload_fails_closed():
    comment_command = _command("cmd-conflict")
    contents_command = _command(
        "cmd-conflict",
        operation="bridge.project.resume",
        arguments={"project": "auto_uv"},
    )
    transport = _hybrid_transport([comment_command], [contents_command])

    assert transport.initialize_message_mode() == "issue_channel_v5"
    with pytest.raises(RuntimeError, match="COMMAND_IDENTITY_CONFLICT"):
        transport.fetch_commands()
