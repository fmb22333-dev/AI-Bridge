from types import SimpleNamespace

from ai_bridge.security.secret_store import MemorySecretStore
from ai_bridge.transport.base import TransportHealth
from ai_bridge.transport.remote_config import GitHubRemoteConfig, save_remote_config
from ai_bridge.transport.remote_controller import RemoteController
import ai_bridge.transport.supabase_extension  # noqa: F401


class _HealthyTransport:
    def __init__(self, config, secret):
        self.config = config
        self.secret = secret

    def health(self):
        return TransportHealth(True, "ok")


def test_live_supabase_extension_inherits_primary_bridge_id(tmp_path):
    save_remote_config(
        tmp_path / "remote.json",
        GitHubRemoteConfig(repository="owner/bus", branch="main", bridge_id="bridge-live"),
    )
    state = {}
    secrets = MemorySecretStore()
    remote = RemoteController(
        service=SimpleNamespace(db=SimpleNamespace()),
        data_dir=tmp_path,
        runtime_state=state,
        secret_store=secrets,
    )
    remote.fallback_controller.transport_factory = lambda config, secret: _HealthyTransport(config, secret)
    remote.fallback_controller._start_loop = lambda config, transport: None
    result = remote.configure_supabase(
        project_url="https://example.supabase.co",
        secret_key="sb_secret_test",
        poll_interval_seconds=0.5,
    )
    assert result["bridge_id"] == "bridge-live"
    assert result["credential_saved"] is True
    assert remote.test_supabase()["ok"] is True
    assert remote.disconnect_supabase()["status"] == "disabled"
