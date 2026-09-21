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


def test_step2_runs_as_background_task_and_client_has_bounded_observation():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "setup.html").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")

    assert '@app.post("/setup/provision/start", include_in_schema=False)' in routes
    assert "threading.Thread" in routes
    assert "already_running" in routes
    assert 'fetch(\'/setup/provision/start\'' in html
    assert "120000" in html
    assert "不会重复创建已存在的安全 Bus" in html
    assert "require_initial_presence=False" in routes


def test_step2_failure_keeps_real_stage_and_renders_permission_remediation():
    html = (ROOT / "src" / "ai_bridge" / "web" / "templates" / "setup.html").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")
    assert 'failed_stage = str(prior.get("stage") or "failed")' in routes
    assert "REPOSITORY_CREATE_PERMISSION_DENIED" in html
    assert "GitHub 身份已经验证成功，但当前凭据没有创建这个 Private Repository 的权限" in html
    assert "尚未验证“创建新仓库”权限" in html
