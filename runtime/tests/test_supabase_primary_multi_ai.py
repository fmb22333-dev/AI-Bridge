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
    assert state["role"] == "primary_fast"
    assert state["multi_channel"] is True
    assert state["multi_ai"] is True
    assert state["parallel_with_fallback"] is True
    assert state["same_session_serial"] is True
    assert state["max_execution_lanes"] == 8


def test_public_runner_contains_bounded_lane_scheduler():
    root = Path(__file__).resolve().parents[1]
    runner = (root / "src/ai_bridge/transport/runner.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=8" in runner
    assert 'f"session:{command.session}"' in runner
    assert 'f"adapter:{command.adapter}"' in runner
    assert 'state.get("multi_channel") is True' in runner


def test_ui_declares_supabase_primary_and_github_fallback():
    root = Path(__file__).resolve().parents[1]
    setup = (root / "src/ai_bridge/web/static/fallback_setup.js").read_text(encoding="utf-8")
    dashboard = (root / "src/ai_bridge/web/static/fallback_dashboard.js").read_text(encoding="utf-8")
    assert "Supabase Primary Bus" in setup
    assert "GitHub Bus 作为备用命令通道" in setup
    assert "Supabase Primary Bus" in dashboard
    assert "GitHub fallback" in dashboard


def test_presence_extension_declares_routing_authority():
    root = Path(__file__).resolve().parents[1]
    extension = (root / "src/ai_bridge/transport/supabase_primary_extension.py").read_text(encoding="utf-8")
    assert '"primary": "supabase"' in extension
    assert '"fallback": "github_v5"' in extension
    assert '"authority": "github"' in extension
    assert '"command_id_preserved_on_failover": True' in extension
