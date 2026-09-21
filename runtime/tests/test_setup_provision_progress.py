from __future__ import annotations

from types import SimpleNamespace

from ai_bridge.transport.remote_config import GitHubRemoteConfig
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.security.secret_store import MemorySecretStore
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.core.service import BridgeService
from ai_bridge.transport.base import TransportHealth


class _Transport:
    def __init__(self):
        self.initialize_calls = 0
        self.presence = []

    def health(self):
        return TransportHealth(True, "ok")

    def initialize_message_mode(self):
        self.initialize_calls += 1
        return "issue_mailbox_v3"

    def publish_presence(self, payload):
        self.presence.append(payload)

    def message_state(self):
        return {"mode": "auto"}

    def rate_snapshot(self):
        return {}

    def fetch_commands(self):
        return []


def test_setup_can_defer_message_mode_probe_from_connection_critical_path(tmp_path):
    service = BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=WorkspaceRegistry())
    transport = _Transport()
    controller = RemoteController(
        service=service,
        data_dir=tmp_path,
        runtime_state={},
        secret_store=MemorySecretStore(),
        transport_factory=lambda config, token: transport,
    )
    controller._start_github_loop_locked = lambda config, value: None

    result = controller.configure_github(
        GitHubRemoteConfig(repository="owner/repo", branch="main", bridge_id="bridge"),
        "token",
        initialize_message_mode=False,
    )

    assert result["status"] == "connected"
    assert transport.initialize_calls == 0
    assert transport.presence


def test_supabase_extension_wrapper_preserves_configure_keyword_options():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src" / "ai_bridge" / "transport" / "supabase_extension.py"
    ).read_text(encoding="utf-8")
    assert "def _extended_configure_github(self, config, token, **kwargs):" in source
    assert "_original_configure_github(self, config, token, **kwargs)" in source
