from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "bootstrap" / "supervisor" / "0.1.2" / "_System" / "supervisor.py"


def test_supervisor_defaults_to_shared_product_repository_not_user_bus():
    text = SUPERVISOR.read_text(encoding="utf-8")
    assert 'PRODUCT_REPOSITORY = "fmb22333-dev/AI-Bridge"' in text
    assert 'PRODUCT_BRANCH = "main"' in text
    assert '"repository": PRODUCT_REPOSITORY' in text
    assert '"branch": PRODUCT_BRANCH' in text
    assert '"manifest_path": "runtime-release.json"' in text


def test_supervisor_does_not_bootstrap_runtime_release_into_user_bus():
    text = SUPERVISOR.read_text(encoding="utf-8")
    assert '"bootstrap_from_bus": True' not in text
    assert 'return {\n                "repository": repository,\n                "branch": "bridge-runtime"' not in text
