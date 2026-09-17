from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen


SOURCE_COMMIT = "ff068d4313d8035876dad3c584d3ee774a03c3a0"
RAW = f"https://raw.githubusercontent.com/fmb22333-dev/AI-Bridge/{SOURCE_COMMIT}/runtime/"


def _download(relative: str) -> str:
    with urlopen(RAW + relative, timeout=20) as response:
        return response.read().decode("utf-8")


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


EXTENSION = r'''from __future__ import annotations

from ai_bridge.transport.fallback_controller import (
    SupabaseFallbackConfig,
    SupabaseFallbackController,
)
from ai_bridge.transport.remote_config import default_bridge_id, load_remote_config
from ai_bridge.transport.remote_controller import RemoteConfigurationError, RemoteController


if not getattr(RemoteController, "_supabase_extension_installed", False):
    _original_init = RemoteController.__init__
    _original_presence_core = RemoteController._presence_core
    _original_configure_github = RemoteController.configure_github

    def _extended_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        try:
            saved_primary = load_remote_config(self.config_path)
        except Exception:
            saved_primary = None
        bridge_hint = saved_primary.bridge_id if saved_primary is not None else default_bridge_id()
        self.fallback_controller = SupabaseFallbackController(
            service=self.service,
            data_dir=self.data_dir,
            runtime_state=self.runtime_state,
            secret_store=self.secret_store,
            bridge_id_hint=bridge_hint,
        )
        self.fallback_controller.start_saved_or_environment()

    def _extended_presence_core(self, bridge_id: str, transport=None) -> dict:
        core = _original_presence_core(self, bridge_id, transport)
        core["bridge_version"] = "0.2.6.76"
        core["fallback_transport"] = dict(self.runtime_state.get("fallback_transport") or {})
        return core

    def _primary_bridge_id(self) -> str:
        try:
            config = load_remote_config(self.config_path)
        except Exception:
            config = None
        if config is not None and str(config.bridge_id or "").strip():
            return str(config.bridge_id).strip()
        remote = self.runtime_state.get("remote")
        if isinstance(remote, dict) and remote.get("configured"):
            bridge_id = str(remote.get("bridge_id") or "").strip()
            if bridge_id:
                return bridge_id
        raise RemoteConfigurationError(
            "Connect the primary GitHub Bus before configuring the Supabase backup transport"
        )

    def _refresh_presence_after_fallback_change(self) -> None:
        if self._transport is None:
            return
        try:
            config = load_remote_config(self.config_path)
            if config is not None:
                self._last_presence_hash = None
                self._publish_presence_if_changed(self._transport, config, force=True)
        except Exception:
            pass

    def configure_supabase(
        self,
        *,
        project_url: str,
        secret_key: str = "",
        poll_interval_seconds: float = 0.5,
        table: str = "ai_bridge_commands",
    ) -> dict:
        bridge_id = _primary_bridge_id(self)
        secret_key = str(secret_key or "").strip()
        if not secret_key:
            secret_key = str(self.secret_store.get(self.fallback_controller.SECRET_NAME) or "").strip()
        if not secret_key:
            raise RemoteConfigurationError("Supabase secret key is required")
        try:
            result = self.fallback_controller.configure(
                SupabaseFallbackConfig(
                    project_url=project_url,
                    bridge_id=bridge_id,
                    table=table,
                    poll_interval_seconds=poll_interval_seconds,
                ),
                secret_key,
            )
        except (ValueError, RuntimeError) as exc:
            raise RemoteConfigurationError(str(exc)) from exc
        self.fallback_controller.bridge_id_hint = bridge_id
        _refresh_presence_after_fallback_change(self)
        return result

    def supabase_state(self) -> dict:
        return self.fallback_controller.public_state()

    def test_supabase(self) -> dict:
        try:
            return self.fallback_controller.test_saved_connection()
        except (ValueError, RuntimeError) as exc:
            raise RemoteConfigurationError(str(exc)) from exc

    def disconnect_supabase(self) -> dict:
        self.fallback_controller.disconnect()
        _refresh_presence_after_fallback_change(self)
        return self.fallback_controller.public_state()

    def _extended_configure_github(self, config, token):
        result = _original_configure_github(self, config, token)
        if hasattr(self, "fallback_controller"):
            self.fallback_controller.bridge_id_hint = str(config.bridge_id or "").strip()
        return result

    RemoteController.__init__ = _extended_init
    RemoteController._presence_core = _extended_presence_core
    RemoteController._primary_bridge_id = _primary_bridge_id
    RemoteController._refresh_presence_after_fallback_change = _refresh_presence_after_fallback_change
    RemoteController.configure_supabase = configure_supabase
    RemoteController.supabase_state = supabase_state
    RemoteController.test_supabase = test_supabase
    RemoteController.disconnect_supabase = disconnect_supabase
    RemoteController.configure_github = _extended_configure_github
    RemoteController.configure = _extended_configure_github
    RemoteController._supabase_extension_installed = True
'''


LIVE_TEST = r'''from types import SimpleNamespace

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
'''


def test_apply_supabase_ui_forward_port():
    root = Path(__file__).resolve().parents[1]
    copies = [
        "src/ai_bridge/transport/supabase_bus.py",
        "src/ai_bridge/transport/fallback_controller.py",
        "src/ai_bridge/web/fallback_routes.py",
        "src/ai_bridge/web/static/fallback_setup.js",
        "src/ai_bridge/web/static/fallback_dashboard.js",
        "tests/test_supabase_fallback_transport.py",
    ]
    for relative in copies:
        text = _download(relative)
        if relative == "src/ai_bridge/web/fallback_routes.py":
            needle = "from ai_bridge.transport.remote_controller import RemoteConfigurationError\n"
            replacement = needle + "from ai_bridge.transport import supabase_extension as _supabase_extension  # noqa: F401\n"
            if "supabase_extension" not in text:
                text = text.replace(needle, replacement)
        _write(root, relative, text)

    _write(root, "src/ai_bridge/transport/supabase_extension.py", EXTENSION)
    _write(root, "tests/test_supabase_live_extension.py", LIVE_TEST)

    app_path = root / "src/ai_bridge/app.py"
    app = app_path.read_text(encoding="utf-8")
    import_line = "from ai_bridge.web.fallback_routes import install_fallback_routes\n"
    if import_line not in app:
        app = app.replace(
            "from ai_bridge.web.routes import install_control_routes\n",
            import_line + "from ai_bridge.web.routes import install_control_routes\n",
        )
    call = '''    install_fallback_routes(\n        app,\n        auth_token=connection["token"],\n        web_root=web_root,\n    )\n'''
    marker = "    install_control_routes(\n"
    if call not in app:
        app = app.replace(marker, call + marker, 1)
    app_path.write_text(app, encoding="utf-8")

    assert (root / "src/ai_bridge/transport/supabase_bus.py").exists()
    assert (root / "src/ai_bridge/transport/supabase_extension.py").exists()
    assert "install_fallback_routes" in app
