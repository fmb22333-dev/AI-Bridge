from __future__ import annotations

from ai_bridge.transport.remote_controller import RemoteController


_INSTALLED = False
_BASE_PRESENCE_CORE = RemoteController._presence_core
_BASE_PRIMARY_BRIDGE_ID = RemoteController._primary_bridge_id


def install_supabase_primary_extension() -> None:
    """Promote Supabase semantics without rewriting the legacy controller.

    Compatibility names (`fallback_transport`, `/control/fallback/*`, and the
    fallback config file) intentionally survive Runtime 0.2.6.x.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    def _presence_core(self, bridge_id: str, transport=None) -> dict:
        core = _BASE_PRESENCE_CORE(self, bridge_id, transport)
        core["bridge_version"] = "0.2.6.56"
        supabase = dict(self.runtime_state.get("fallback_transport") or {})
        if supabase.get("configured"):
            supabase.setdefault("role", "primary_fast")
            supabase.setdefault("multi_channel", True)
            supabase.setdefault("multi_ai", True)
            supabase.setdefault("same_session_serial", True)
            supabase.setdefault("max_execution_lanes", 8)
            supabase.setdefault("parallel_with_fallback", True)
        core["fallback_transport"] = dict(supabase)  # compatibility alias
        core["supabase_transport"] = dict(supabase)
        message = core.get("message_transport")
        if isinstance(message, dict):
            message["role"] = "fallback_command_authority"
        core["command_transport_policy"] = {
            "primary": "supabase",
            "fallback": "github_v5",
            "authority": "github",
            "multi_ai": True,
            "same_session_serial": True,
            "cross_lane_parallel": True,
            "max_execution_lanes": 8,
            "command_id_preserved_on_failover": True,
        }
        return core

    def _primary_bridge_id(self) -> str:
        # Keep GitHub as the durable Bridge-ID authority even though command
        # traffic normally enters through Supabase.
        return _BASE_PRIMARY_BRIDGE_ID(self)

    RemoteController._presence_core = _presence_core
    RemoteController._primary_bridge_id = _primary_bridge_id
    _INSTALLED = True
