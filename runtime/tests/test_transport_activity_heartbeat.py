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
