from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_keeps_manual_flow_and_adds_clean_one_click_provision():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "setup.html").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")

    assert 'id="provisionRepo"' in html
    assert "一键创建干净 Bus 并连接" in html
    assert 'id="verifyCredential"' in html
    assert 'id="nextStep"' in html
    assert 'id="githubLogin"' not in html
    assert '@app.post("/setup/github-credential-test", include_in_schema=False)' in routes
    assert 'id="save"' in html
    assert "连接已有 GitHub Bus" in html
    assert '@app.post("/setup/provision", include_in_schema=False)' in routes
    assert '@app.get("/setup/provision/state", include_in_schema=False)' in routes
    assert "pollProvisionState" in html
    assert '@app.post("/setup/save", include_in_schema=False)' in routes
    assert "不会复制" in html


def test_clean_provision_separates_bus_from_runtime_source():
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")
    assert '"runtime_source_repository": runtime_source_repository()' in routes
    assert '"branch": "main"' in routes
    assert '"manifest_path": "runtime-release.json"' in routes
    assert '"branch": "bridge-runtime"' not in routes
    assert '"manifest_path": "distribution-release.json"' not in routes
    assert '"bootstrap_from_bus": False' in routes
    assert 'update_source.json' in routes
