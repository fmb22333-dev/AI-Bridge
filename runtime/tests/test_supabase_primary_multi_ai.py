from pathlib import Path

from ai_bridge.transport.supabase_bus import SupabaseBusConfig, SupabaseBusTransport


def test_supabase_transport_declares_primary_multi_ai_lane_dispatch():
    transport = SupabaseBusTransport(
        SupabaseBusConfig(
            project_url="https://example.supabase.co",
            secret_key="sb_secret_test",
            bridge_id="bridge-test",
        ),
        client=object(),
    )
    state = transport.message_state()
    assert state["mode"] == "supabase_primary"
    assert state["kind"] == "supabase_primary"
    assert state["role"] == "primary_realtime_command_transport"
    assert state["multi_channel"] is True
    assert state["multi_ai"] is True
    assert state["parallel_with_fallback"] is True
    assert state["compatibility"]["deprecated_fields"]["role"] == "primary_fast"


def test_supabase_ui_is_primary_and_github_is_fallback():
    root = Path(__file__).resolve().parents[1]
    setup = (root / "src/ai_bridge/web/static/fallback_setup.js").read_text(encoding="utf-8")
    dashboard = (root / "src/ai_bridge/web/static/fallback_dashboard.js").read_text(encoding="utf-8")
    assert "Supabase Primary Bus" in setup
    assert "GitHub Bus 作为备用命令通道" in setup
    assert "Supabase Primary Bus" in dashboard
    assert "GitHub fallback" in dashboard
