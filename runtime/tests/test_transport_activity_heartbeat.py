from __future__ import annotations

from types import SimpleNamespace

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.remote_config import GitHubRemoteConfig
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.transport.runner import TransportRunner


class FakeDB:
    def __init__(self):
        self.published = []

    def is_published(self, transport_key, command_id):
        return False

    def save_result(self, result):
        pass

    def mark_published(self, transport_key, command_id):
        self.published.append((transport_key, command_id))


class FakeService:
    def __init__(self):
        self.db = FakeDB()

    def execute(self, command):
        return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={})


class FakeTransport:
    key = "fake"

    def __init__(self, command):
        self.command = command
        self.results = []

    def fetch_commands(self):
        return [self.command]

    def publish_result(self, result):
        self.results.append(result)


class FakeSecrets:
    def get(self, key):
        return "token"


def test_transport_runner_emits_poll_and_execution_activity_events():
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": "cmd-heartbeat-test",
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False, "budget_seconds": 30},
        "risk": "L1",
    })
    events = []
    runner = TransportRunner(FakeService(), FakeTransport(command), activity_observer=lambda event, payload: events.append((event, payload)))

    assert runner.poll_once() == 1
    names = [event for event, _ in events]
    assert names[0] == "poll_started"
    assert "command_started" in names
    assert names[-1] == "poll_finished"
    started = next(payload for event, payload in events if event == "command_started")
    assert started["command_id"] == "cmd-heartbeat-test"
    assert started["operation"] == "bridge.update.status"


def test_remote_controller_exposes_live_activity_in_public_state(tmp_path):
    runtime_state = {}
    controller = RemoteController(service=SimpleNamespace(), data_dir=tmp_path, runtime_state=runtime_state, secret_store=FakeSecrets())
    controller._on_transport_activity("poll_started", {"at": "2026-09-08T20:00:00+00:00"})
    controller._on_transport_activity("command_started", {
        "at": "2026-09-08T20:00:01+00:00",
        "command_id": "cmd-live",
        "operation": "inspect.find",
    })

    state = controller.public_state("connected", GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"))
    activity = state["activity"]
    assert activity["last_poll_at"] == "2026-09-08T20:00:00+00:00"
    assert activity["last_command_received_at"] == "2026-09-08T20:00:01+00:00"
    assert activity["current_execution"]["command_id"] == "cmd-live"

    controller._on_transport_activity("command_finished", {"at": "2026-09-08T20:00:02+00:00", "command_id": "cmd-live"})
    state = controller.public_state("connected", GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"))
    assert state["activity"]["current_execution"] is None

def test_remote_controller_tracks_multiple_active_executions_without_false_idle(tmp_path):
    runtime_state = {}
    controller = RemoteController(service=SimpleNamespace(), data_dir=tmp_path, runtime_state=runtime_state, secret_store=FakeSecrets())
    controller._on_transport_activity("command_started", {"at": "2026-09-14T03:00:00+00:00", "command_id": "cmd-a", "operation": "cook.execute"})
    controller._on_transport_activity("command_started", {"at": "2026-09-14T03:00:01+00:00", "command_id": "cmd-b", "operation": "bridge.update.status"})
    state = controller.public_state("connected", GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"))
    assert state["activity"]["active_execution_count"] == 2
    controller._on_transport_activity("command_finished", {"at": "2026-09-14T03:00:02+00:00", "command_id": "cmd-b"})
    state = controller.public_state("connected", GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"))
    assert state["activity"]["active_execution_count"] == 1
    assert state["activity"]["current_execution"]["command_id"] == "cmd-a"
    controller._on_transport_activity("command_finished", {"at": "2026-09-14T03:00:03+00:00", "command_id": "cmd-a"})
    assert controller.public_state("connected", GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"))["activity"]["active_execution_count"] == 0


def test_transport_runner_replace_transport_resets_contents_bootstrap_state():
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": "cmd-replace-transport",
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })
    old_transport = FakeTransport(command)
    new_transport = FakeTransport(command)
    runner = TransportRunner(FakeService(), old_transport)
    runner._contents_index_restored = True

    assert runner.replace_transport(new_transport) is old_transport
    assert runner.transport is new_transport
    assert runner._contents_index_restored is False
    runner.shutdown()


def test_remote_controller_rebuild_policy_requires_three_generic_failures():
    assert RemoteController._should_rebuild_transport("error", 2) is False
    assert RemoteController._should_rebuild_transport("error", 3) is True
    assert RemoteController._should_rebuild_transport("auth_degraded", 99) is False
    assert RemoteController._should_rebuild_transport("rate_limited", 99) is False


def test_remote_controller_rebuild_transport_replaces_runner_transport(tmp_path):
    command = CommandEnvelope.model_validate({
        "protocol": "bridge/1",
        "command_id": "cmd-rebuild-transport",
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.update.status",
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })

    class Client:
        def __init__(self):
            self.closed = False
        def close(self):
            self.closed = True

    class RecoverableTransport(FakeTransport):
        def __init__(self, command):
            super().__init__(command)
            self.client = Client()
            self.initialized = False
        def health(self):
            return SimpleNamespace(ok=True, detail="connected")
        def initialize_message_mode(self):
            self.initialized = True
            return "issue_channel_v5"

    old_transport = RecoverableTransport(command)
    new_transport = RecoverableTransport(command)
    controller = RemoteController(
        service=FakeService(),
        data_dir=tmp_path,
        runtime_state={},
        secret_store=FakeSecrets(),
        transport_factory=lambda config, token: new_transport,
    )
    controller._transport = old_transport
    runner = TransportRunner(controller.service, old_transport)
    config = GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge")

    rebuilt = controller._rebuild_transport(config, runner)

    assert rebuilt is new_transport
    assert runner.transport is new_transport
    assert controller._transport is new_transport
    assert new_transport.initialized is True
    assert old_transport.client.closed is True
    runner.shutdown()
