from __future__ import annotations

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
        raw_supabase_state = dict(self.runtime_state.get("fallback_transport") or {})
        legacy_supabase_state = dict(raw_supabase_state)
        legacy_supabase_fields = (
            raw_supabase_state.get("compatibility", {}).get("deprecated_fields", {})
            if isinstance(raw_supabase_state.get("compatibility"), dict)
            else {}
        )
        if isinstance(legacy_supabase_fields, dict):
            legacy_supabase_state.update(legacy_supabase_fields)
        legacy_supabase_state.pop("compatibility", None)

        supabase_state = dict(raw_supabase_state)
        if supabase_state.get("kind") not in {None, "none"}:
            supabase_state["kind"] = "supabase_primary"
        if supabase_state.get("configured") or supabase_state.get("role"):
            supabase_state["role"] = "primary_realtime_command_transport"
        supabase_state.setdefault("multi_channel", True)
        supabase_state.setdefault("multi_ai", True)
        core["supabase_transport"] = supabase_state

        github_state = core.get("message_transport")
        if isinstance(github_state, dict):
            legacy_primary_ingress = github_state.pop("primary_ingress", None)
            legacy_fallback_ingress = github_state.pop("fallback_ingress", None)
            existing_compatibility = github_state.get("compatibility")
            existing_deprecated = (
                existing_compatibility.get("deprecated_fields", {})
                if isinstance(existing_compatibility, dict)
                else {}
            )
            deprecated_fields = dict(existing_deprecated) if isinstance(existing_deprecated, dict) else {}
            deprecated_fields.setdefault("role", "fallback_command_authority")
            if legacy_primary_ingress is not None:
                github_state["github_primary_ingress"] = legacy_primary_ingress
                deprecated_fields["primary_ingress"] = legacy_primary_ingress
            if legacy_fallback_ingress is not None:
                github_state["github_fallback_ingress"] = legacy_fallback_ingress
                deprecated_fields["fallback_ingress"] = legacy_fallback_ingress
            github_state["role"] = "fallback_command_transport"
            github_state["compatibility"] = {"deprecated_fields": deprecated_fields}

        core["realtime_command_primary"] = "supabase"
        core["realtime_command_fallback"] = "github_v5"
        core["durable_authority"] = "github"
        core["realtime_command_capabilities"] = {
            "multi_ai": True,
            "same_session_serial": True,
            "cross_lane_parallel": True,
            "workspace_lane_scope": "workspace_id",
            "same_workspace_serial": True,
            "max_execution_lanes": 8,
            "command_id_preserved_on_failover": True,
        }
        core["compatibility"] = {
            "deprecated_transport_fields": {
                "command_transport_policy": {
                    "primary": "supabase",
                    "fallback": "github_v5",
                    "authority": "github",
                },
                "fallback_transport": legacy_supabase_state,
            }
        }
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
            "Connect the GitHub authority/fallback Bus before configuring the Supabase Primary Bus"
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
