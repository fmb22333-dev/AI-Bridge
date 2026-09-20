from pathlib import Path

from ai_bridge.core.service import BridgeService
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.security.secret_store import MemorySecretStore
from ai_bridge.transport.base import TransportHealth
from ai_bridge.transport.remote_config import GitHubRemoteConfig, github_repository_url, github_token_template_url, load_remote_config, normalize_github_repository
from ai_bridge.transport.remote_controller import RemoteController


class FakeTransport:
    key = "fake:github"

    def __init__(self):
        self.presence = []
        self.results = []

    def health(self):
        return TransportHealth(True, "connected")

    def fetch_commands(self):
        return []

    def publish_presence(self, payload):
        self.presence.append(payload)

    def publish_result(self, result):
        self.results.append(result)


def test_github_config_persists_only_after_write_probe(tmp_path: Path):
    service = BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=WorkspaceRegistry())
    secrets = MemorySecretStore()
    runtime = {"remote": {"configured": False, "status": "unconfigured"}}
    fake = FakeTransport()
    controller = RemoteController(
        service=service,
        data_dir=tmp_path,
        runtime_state=runtime,
        secret_store=secrets,
        poll_interval=60,
        transport_factory=lambda config, token: fake,
    )
    config = GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="test-bridge")
    state = controller.configure_github(config, "secret-token")
    assert state["status"] == "connected"
    assert state["kind"] == "github_bus"
    assert fake.presence
    assert load_remote_config(tmp_path / "remote.json") == config
    assert secrets.get("github_bus") == "secret-token"
    controller.disconnect()
    assert secrets.get("github_bus") is None


def test_no_cloudflare_runtime_module():
    import ai_bridge.transport as transport_pkg
    runtime_dir = Path(transport_pkg.__file__).parent
    assert not (runtime_dir / "cloudflare_relay.py").exists()


def test_repository_input_accepts_github_urls():
    assert normalize_github_repository("https://github.com/owner/repo") == "owner/repo"
    assert normalize_github_repository("https://github.com/owner/repo/tree/main") == "owner/repo"
    assert normalize_github_repository("git@github.com:owner/repo.git") == "owner/repo"
    assert normalize_github_repository("github.com/owner/repo") == "owner/repo"
    assert github_repository_url("owner/repo") == "https://github.com/owner/repo"


def _command_payload(command_id: str) -> bytes:
    import json
    return json.dumps({
        "protocol": "bridge/1",
        "command_id": command_id,
        "workspace": "Houdini Test",
        "adapter": "houdini",
        "operation": "inspect.context",
        "session": None,
        "project_file": None,
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "auto", "dry_run": False},
        "risk": "L1",
    }).encode("utf-8")


def test_github_bus_does_not_redownload_history_on_new_command():
    import base64
    import json
    import httpx
    from ai_bridge.transport.github_bus import GitHubBusConfig, GitHubBusTransport

    calls = []
    phase = {"value": 1}

    old = _command_payload("cmd-old")
    new = _command_payload("cmd-new")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        path = request.url.path
        if path.endswith("/contents/.ai-bridge/commands/bridge-test"):
            if phase["value"] == 1:
                return httpx.Response(200, headers={"ETag": '"v1"'}, json=[
                    {"type": "file", "name": "cmd-old.json", "sha": "sha-old"},
                ])
            return httpx.Response(200, headers={"ETag": '"v2"'}, json=[
                {"type": "file", "name": "cmd-new.json", "sha": "sha-new"},
                {"type": "file", "name": "cmd-old.json", "sha": "sha-old"},
            ])
        if path.endswith("/contents/.ai-bridge/results/bridge-test"):
            return httpx.Response(200, json=[
                {"type": "file", "name": "cmd-old.json", "sha": "result-old"},
            ])
        if path.endswith("/contents/.ai-bridge/commands/bridge-test/cmd-old.json"):
            return httpx.Response(200, json={"content": base64.b64encode(old).decode("ascii")})
        if path.endswith("/contents/.ai-bridge/commands/bridge-test/cmd-new.json"):
            return httpx.Response(200, json={"content": base64.b64encode(new).decode("ascii")})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=client,
    )

    # Startup sees old command + old result, so it should not download the old command JSON.
    assert transport.fetch_commands() == []
    assert not any("cmd-old.json" in url for _, url in calls)

    calls.clear()
    phase["value"] = 2
    commands = transport.fetch_commands()
    assert [c.command_id for c in commands] == ["cmd-new"]
    assert sum("cmd-new.json" in url for _, url in calls) == 1
    assert not any("cmd-old.json" in url for _, url in calls)
    # Result directory is only needed to bootstrap the initial history index.
    assert not any("/results/bridge-test" in url for _, url in calls)


def test_github_result_publish_skips_preflight_get():
    import json
    import httpx
    from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
    from ai_bridge.transport.github_bus import GitHubBusConfig, GitHubBusTransport

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.url.path.endswith("/contents/.ai-bridge/results/bridge-test/cmd-fast.json")
        if request.method == "PUT":
            body = json.loads(request.content.decode("utf-8"))
            assert "sha" not in body
            return httpx.Response(201, json={"content": {"sha": "new-result-sha"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=client,
    )
    transport.publish_result(
        ExecutionResult(command_id="cmd-fast", status=ExecutionStatus.SUCCESS)
    )
    assert calls == ["PUT"]


def test_default_github_poll_interval_is_subsecond(tmp_path: Path):
    service = BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=WorkspaceRegistry())
    controller = RemoteController(
        service=service,
        data_dir=tmp_path,
        runtime_state={"remote": {}},
        secret_store=MemorySecretStore(),
    )
    assert controller.poll_interval == 0.5


def test_runner_adds_bridge_timing_metadata(tmp_path: Path):
    from ai_bridge.protocol.command import CommandEnvelope
    from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
    from ai_bridge.transport.runner import TransportRunner

    class DB:
        def is_published(self, key, command_id): return False
        def mark_published(self, key, command_id): self.marked = (key, command_id)

    class Service:
        def __init__(self): self.db = DB()
        def execute(self, command):
            return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS)

    class Transport:
        key = "test:key"
        def __init__(self): self.published = None
        def fetch_commands(self):
            return [CommandEnvelope(
                command_id="cmd-timing",
                workspace="ws",
                adapter="houdini",
                operation="inspect.context",
            )]
        def publish_result(self, result): self.published = result

    service = Service()
    transport = Transport()
    assert TransportRunner(service, transport).poll_once() == 1
    timing = transport.published.result["_bridge"]["timing"]
    assert timing["bridge_received_at"].endswith("+00:00")
    assert timing["result_publish_started_at"].endswith("+00:00")
    assert timing["execute_ms"] >= 0


def test_conditional_poll_tracks_304_and_rate_limit():
    import httpx
    from ai_bridge.transport.github_bus import GitHubBusConfig, GitHubBusTransport

    seen_if_none_match = []
    phase = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4900",
            "X-RateLimit-Reset": "2000000000",
        }
        if request.url.path.endswith("/contents/.ai-bridge/results/bridge-test"):
            return httpx.Response(404, headers=headers)
        assert request.url.path.endswith("/contents/.ai-bridge/commands/bridge-test")
        seen_if_none_match.append(request.headers.get("If-None-Match"))
        phase["value"] += 1
        if phase["value"] == 1:
            headers["ETag"] = '"commands-v1"'
            return httpx.Response(200, headers=headers, json=[])
        return httpx.Response(304, headers=headers)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = GitHubBusTransport(
        GitHubBusConfig(repository="owner/repo", token="token", bridge_id="bridge-test"),
        client=client,
    )
    assert transport.fetch_commands() == []
    assert transport.fetch_commands() == []
    assert seen_if_none_match == [None, '"commands-v1"']
    rate = transport.rate_snapshot()
    assert rate["remaining"] == 4900
    assert rate["limit"] == 5000
    assert rate["polls"] == 2
    assert rate["not_modified"] == 1
    assert rate["not_modified_ratio"] == 0.5


def test_poll_interval_backs_off_when_rate_limit_is_low(tmp_path: Path):
    service = BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=WorkspaceRegistry())
    controller = RemoteController(
        service=service,
        data_dir=tmp_path,
        runtime_state={"remote": {}},
        secret_store=MemorySecretStore(),
    )

    class RateTransport:
        def __init__(self, remaining, limit=5000):
            self.remaining = remaining
            self.limit = limit
        def rate_snapshot(self):
            return {"remaining": self.remaining, "limit": self.limit, "reset": None}

    assert controller._effective_poll_interval(RateTransport(4500)) == 0.5
    assert controller._effective_poll_interval(RateTransport(900)) == 1.0
    assert controller._effective_poll_interval(RateTransport(400)) == 2.0
    assert controller._effective_poll_interval(RateTransport(80)) >= 5.0


def test_one_click_token_template_requests_repo_creation_permission():
    url = github_token_template_url("owner")
    assert "administration=write" in url
    assert "contents=write" in url
    assert "issues=write" in url
