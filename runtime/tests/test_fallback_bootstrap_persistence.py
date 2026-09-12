import base64
import json

import httpx

import ai_bridge.transport.github_bus as gb
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.transport.runner import TransportRunner


def _command(command_id):
    return CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })


def _contents_transport(files):
    def handler(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("/contents/.ai-bridge/commands/bridge-test"):
            return httpx.Response(200, headers={"ETag": '"idx"'}, json=[
                {"type": "file", "name": name, "sha": sha}
                for name, (sha, _raw) in sorted(files.items())
            ])
        if request.method == "GET" and path.endswith("/contents/.ai-bridge/results/bridge-test"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and "/contents/.ai-bridge/commands/bridge-test/" in path:
            name = path.rsplit("/", 1)[-1]
            raw = files[name][1]
            return httpx.Response(200, json={"content": base64.b64encode(raw).decode("ascii")})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = gb.GitHubBusTransport(
        gb.GitHubBusConfig(repository="owner/repo", token="x", bridge_id="bridge-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transport._message_mode = "issue_channel_v5"
    return transport


def _payload(command_id):
    return json.dumps(_command(command_id).model_dump(mode="json")).encode()


def test_first_hybrid_contents_poll_seeds_existing_files_without_executing():
    files = {"legacy.json": ("sha-old", _payload("cmd-legacy"))}
    transport = _contents_transport(files)

    assert transport._fetch_contents_commands() == []
    assert transport.contents_command_index_snapshot() == {"legacy.json": "sha-old"}


def test_restored_hybrid_index_detects_only_new_sha():
    files = {
        "legacy.json": ("sha-old", _payload("cmd-legacy")),
        "new.json": ("sha-new", _payload("cmd-new")),
    }
    transport = _contents_transport(files)
    transport.restore_contents_command_index({"legacy.json": "sha-old"})

    commands = transport._fetch_contents_commands()
    assert [item.command_id for item in commands] == ["cmd-new"]


def test_contents_index_roundtrips_through_db(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    key = "github:owner/repo:main:bridge-test"
    assert db.get_transport_contents_index(key) is None
    db.set_transport_contents_index(key, {"legacy.json": "sha-old"})
    assert db.get_transport_contents_index(key) == {"legacy.json": "sha-old"}


class _IndexTransport:
    key = "github:owner/repo:main:bridge-test"
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.restored = "unset"
    def restore_contents_command_index(self, index):
        self.restored = index
    def contents_command_index_snapshot(self):
        return dict(self.snapshot)
    def fetch_commands(self):
        return []


class _Service:
    def __init__(self, db):
        self.db = db


def test_runner_persists_transport_index_for_restart(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    first = _IndexTransport({"legacy.json": "sha-old"})
    assert TransportRunner(_Service(db), first).poll_once() == 0
    assert first.restored is None
    assert db.get_transport_contents_index(first.key) == {"legacy.json": "sha-old"}

    second = _IndexTransport({"legacy.json": "sha-old"})
    assert TransportRunner(_Service(db), second).poll_once() == 0
    assert second.restored == {"legacy.json": "sha-old"}
