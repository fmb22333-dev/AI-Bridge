from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "bootstrap" / "supervisor" / "0.1.3" / "supervisor_update_source.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("supervisor_update_source", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supervisor_defaults_to_shared_product_repository_not_user_bus():
    module = _load_module()
    config = module.resolve_update_source(
        explicit=None,
        remote={"repository": "alice/alice-ai-bridge-bus", "branch": "main"},
    )

    assert config["repository"] == "fmb22333-dev/AI-Bridge"
    assert config["branch"] == "main"
    assert config["manifest_path"] == "runtime-release.json"
    assert config["bootstrap_from_bus"] is False


def test_explicit_update_source_is_preserved_without_bus_bootstrap():
    module = _load_module()
    config = module.resolve_update_source(
        explicit={
            "repository": "example/ai-bridge-mirror",
            "branch": "stable",
            "manifest_path": "release.json",
            "bootstrap_from_bus": True,
        },
        remote={"repository": "alice/alice-ai-bridge-bus"},
    )

    assert config["repository"] == "example/ai-bridge-mirror"
    assert config["branch"] == "stable"
    assert config["manifest_path"] == "release.json"
    assert config["bootstrap_from_bus"] is False
