from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "bootstrap" / "supervisor" / "0.1.4" / "_System" / "supervisor.py"
README = ROOT / "bootstrap" / "supervisor" / "0.1.4" / "README_FIRST.txt"


def test_supervisor_014_uses_shared_product_authority_and_forbids_bus_bootstrap():
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert '"repository": "fmb22333-dev/AI-Bridge"' in source
    assert '"branch": "main"' in source
    assert '"manifest_path": "runtime-release.json"' in source
    assert 'config["bootstrap_from_bus"] = False' in source
    assert "BUS_RUNTIME_BOOTSTRAP_FORBIDDEN" in source
    assert "fmb22333-dev/ai-bridge-bus" not in source
    assert '"branch": "bridge-runtime"' not in source
    assert '"manifest_path": "distribution-release.json"' not in source


def test_supervisor_readme_describes_product_bus_separation():
    text = README.read_text(encoding="utf-8")
    assert "fmb22333-dev/AI-Bridge" in text
    assert "用户 Bus 永远不会被自动创建 bridge-runtime 发布分支" in text
    assert "用户 Bus 只负责" in text
