from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_keeps_advanced_budget_collapsed_and_recent_commands_bounded():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")

    assert '<details class="card collapsibleCard" id="executionBudgetPanel">' in html
    assert '<details class="card collapsibleCard" id="executionBudgetPanel" open' not in html
    assert 'id="budgetSummary"' in html
    assert 'id="openCommandHistory"' in html
    assert "const RECENT_COMMAND_LIMIT=5" in html
    assert "最近 5 条" in html
    assert '"/control/commands?limit="' in html


def test_dashboard_recent_commands_show_local_time_purpose_and_progress_without_sender_changes():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    command_protocol = (ROOT / "src" / "ai_bridge" / "protocol" / "command.py").read_text(encoding="utf-8")

    assert "本机时间" in html
    assert "目的" in html
    assert "进度 / 耗时" in html
    assert "function commandTimeText" in html
    assert "toLocaleTimeString" in html
    assert "function commandPurpose" in html
    assert "function commandProgress" in html
    assert "疑似卡住" in html
    assert "budget_seconds" in html
    assert "/control/commands/${encodeURIComponent(item.command_id)}" in html
    assert "purpose" not in command_protocol


def test_dashboard_shows_transport_heartbeat_and_localized_recent_command_labels():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="activityHeartbeat"' in html
    assert "最后轮询" in html
    assert "最后收到命令" in html
    assert "当前状态" in html
    assert "function renderActivityHeartbeat" in html
    assert "Bridge 通讯异常" in html
    assert "<th>操作</th>" in html
    assert "<th>状态</th>" in html
    assert "<th>证据</th>" in html
    assert "function commandStatusText" in html
    assert '<small class="mono">${esc(item.command_id)}</small>' not in html


def test_command_history_is_a_separate_detailed_view():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "commands.html").read_text(encoding="utf-8")
    js = (ROOT / "src" / "ai_bridge" / "web" / "static" / "commands.js").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")

    assert "命令历史" in html
    assert 'id="historyFilter"' in html
    assert 'id="historyLimit"' in html
    assert '"/control/commands?limit="' in js
    assert '@app.get("/commands", include_in_schema=False)' in routes
    assert '@app.get("/control/commands", include_in_schema=False)' in routes
    assert 'service.db.list_commands(limit)' in routes
    assert '@app.get("/control/commands/{command_id}", include_in_schema=False)' in routes


def test_dashboard_uses_grouped_layout_without_removing_plugin_controls():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "src" / "ai_bridge" / "web" / "static" / "app.css").read_text(encoding="utf-8")

    assert "overviewGrid" in html
    assert "twoColGrid" in html
    assert "本地快捷入口" in html
    assert 'id="installAllPlugins"' in html
    assert ".overviewGrid" in css
    assert ".collapsibleCard" in css


def test_dashboard_hides_historical_sessions_but_keeps_live_session_panel():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "src" / "ai_bridge" / "web" / "static" / "app.css").read_text(encoding="utf-8")

    assert '<div id="sessions"></div>' in html
    assert "#sessions .historyHeader" in css
    assert "#sessions .session.offline" in css
    assert "display:none" in css

def test_dashboard_keeps_busy_unknown_session_visible():
    js = (ROOT / "src" / "ai_bridge" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "src" / "ai_bridge" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert 'i.state==="connected"||i.state==="busy_unknown"' in js
    assert "BUSY UNKNOWN" in js
    assert "busyUnknown" in js
    assert "#sessions .session.busyUnknown" in css
    assert "#sessions .session.offline{display:none}" in css
