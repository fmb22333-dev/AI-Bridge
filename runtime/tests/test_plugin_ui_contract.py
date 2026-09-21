from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_ui_exposes_unreal_project_install_and_install_all():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "src" / "ai_bridge" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")

    assert 'id="installAllPlugins"' in html
    assert "Host 插件" in html
    assert "pluginInstall" in js
    assert 'data-project=' in js
    assert "project_file=" in js
    assert "_unreal_project_files" in routes
    assert "unreal_projects=" in routes


def test_dashboard_static_assets_are_unversioned_and_no_store():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")

    assert 'href="/control/app.css"' in html
    assert 'src="/control/app.js"' in html
    assert "?v=" not in html
    assert "Cache-Control" in routes
    assert "no-store, no-cache, must-revalidate, max-age=0" in routes
