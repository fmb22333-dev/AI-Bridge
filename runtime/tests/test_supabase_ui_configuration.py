from pathlib import Path
from types import SimpleNamespace

from ai_bridge.security.secret_store import MemorySecretStore
from ai_bridge.transport.fallback_controller import (
    SupabaseFallbackConfig,
    SupabaseFallbackController,
)
from ai_bridge.transport.remote_config import GitHubRemoteConfig, save_remote_config
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.transport.base import TransportHealth


class _HealthyTransport:
    def __init__(self, config, secret_key):
        self.config = config
        self.secret_key = secret_key

    def health(self):
        return TransportHealth(True, "ok")


class _UnhealthyTransport(_HealthyTransport):
    def health(self):
        return TransportHealth(False, "offline")


def _service():
    return SimpleNamespace(db=SimpleNamespace())


def test_fallback_controller_disconnect_clears_saved_config_and_secret(tmp_path):
    state = {}
    secrets = MemorySecretStore()
    controller = SupabaseFallbackController(
        service=_service(),
        data_dir=tmp_path,
        runtime_state=state,
        secret_store=secrets,
        bridge_id_hint="bridge-primary",
        transport_factory=lambda config, secret: _HealthyTransport(config, secret),
    )
    controller._start_loop = lambda config, transport: None

    controller.configure(
        SupabaseFallbackConfig(
            project_url="https://example.supabase.co",
            bridge_id="bridge-primary",
            poll_interval_seconds=0.5,
        ),
        "sb_secret_test",
    )
    assert controller.config_path.exists()
    assert secrets.get(controller.SECRET_NAME) == "sb_secret_test"

    controller.disconnect()

    assert not controller.config_path.exists()
    assert secrets.get(controller.SECRET_NAME) is None
    assert state["fallback_transport"] == {
        "configured": False,
        "status": "disabled",
        "kind": "none",
    }


def test_fallback_controller_can_retest_saved_connection_without_exposing_secret(tmp_path):
    state = {}
    secrets = MemorySecretStore()
    controller = SupabaseFallbackController(
        service=_service(),
        data_dir=tmp_path,
        runtime_state=state,
        secret_store=secrets,
        bridge_id_hint="bridge-primary",
        transport_factory=lambda config, secret: _HealthyTransport(config, secret),
    )
    controller._start_loop = lambda config, transport: None
    controller.configure(
        SupabaseFallbackConfig(
            project_url="https://example.supabase.co",
            bridge_id="bridge-primary",
            poll_interval_seconds=0.5,
        ),
        "sb_secret_test",
    )

    result = controller.test_saved_connection()

    assert result["ok"] is True
    assert result["detail"] == "ok"
    assert result["project_url"] == "https://example.supabase.co"
    assert result["credential_saved"] is True
    assert "secret" not in result
    assert "key" not in result


def test_remote_controller_supabase_configuration_inherits_github_bridge_id_and_saved_secret(tmp_path):
    save_remote_config(
        tmp_path / "remote.json",
        GitHubRemoteConfig(
            repository="owner/bus",
            branch="main",
            bridge_id="bridge-from-github",
        ),
    )
    state = {}
    secrets = MemorySecretStore()
    remote = RemoteController(
        service=_service(),
        data_dir=tmp_path,
        runtime_state=state,
        secret_store=secrets,
    )
    remote.fallback_controller.transport_factory = (
        lambda config, secret: _HealthyTransport(config, secret)
    )
    remote.fallback_controller._start_loop = lambda config, transport: None

    first = remote.configure_supabase(
        project_url="https://example.supabase.co",
        secret_key="sb_secret_first",
        poll_interval_seconds=0.5,
    )
    second = remote.configure_supabase(
        project_url="https://example.supabase.co",
        secret_key="",
        poll_interval_seconds=0.25,
    )

    assert first["bridge_id"] == "bridge-from-github"
    assert second["bridge_id"] == "bridge-from-github"
    assert second["poll_interval_seconds"] == 0.25
    assert secrets.get(remote.fallback_controller.SECRET_NAME) == "sb_secret_first"


def test_supabase_ui_contract_is_present_on_setup_and_dashboard():
    root = Path(__file__).resolve().parents[1]
    setup = (root / "src/ai_bridge/web/templates/setup.html").read_text(encoding="utf-8")
    index = (root / "src/ai_bridge/web/templates/index.html").read_text(encoding="utf-8")
    app_js = (root / "src/ai_bridge/web/static/app.js").read_text(encoding="utf-8")
    routes = (root / "src/ai_bridge/web/routes.py").read_text(encoding="utf-8")

    for marker in (
        "Supabase Backup Bus",
        'id="supabaseUrl"',
        'id="supabaseSecret"',
        'id="supabasePoll"',
        'id="connectSupabase"',
    ):
        assert marker in setup

    assert 'id="fallbackRemote"' in index
    assert 'id="testFallbackRemote"' in index
    assert 'id="disconnectFallbackRemote"' in index
    assert "fallbackRemoteCard" in app_js

    assert '"/setup/supabase"' in routes
    assert '"/control/fallback/test"' in routes
    assert '"/control/fallback"' in routes
