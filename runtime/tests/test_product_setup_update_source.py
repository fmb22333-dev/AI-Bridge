from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_ui_defaults_runtime_source_to_product_repository():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "setup.html").read_text(encoding="utf-8")
    assert "fmb22333-dev/AI-Bridge" in html
    assert "runtime_source_repository:setupState?.runtime_source_repository||'fmb22333-dev/AI-Bridge'" in html


def test_setup_routes_use_shared_product_repo_not_user_bus_for_updates():
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")
    assert 'Field(default="fmb22333-dev/AI-Bridge", min_length=3)' in routes
    assert 'return "fmb22333-dev/AI-Bridge"' in routes
    assert '"branch": "main"' in routes
    assert '"manifest_path": "runtime-release.json"' in routes
    assert '"branch": "bridge-runtime"' not in routes
    assert '"manifest_path": "distribution-release.json"' not in routes
