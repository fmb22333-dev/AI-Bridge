from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor
from ai_bridge.deployment import publisher_worker


def _seed_runtime(path: Path, version: str):
    path.mkdir(parents=True, exist_ok=True)
    (path / "src" / "ai_bridge" / "deployment").mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        f'[project]\nname="ai-bridge"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def test_publish_delegates_to_staging_worker(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    _seed_runtime(executor.current, "1.2.3")
    _seed_runtime(executor.staging, "1.2.3")

    called = {}

    def fake_set_version(version):
        called["set_version"] = version
        text = (executor.staging / "pyproject.toml").read_text(encoding="utf-8")
        (executor.staging / "pyproject.toml").write_text(
            text.replace('version = "1.2.3"', f'version = "{version}"'),
            encoding="utf-8",
        )

    def fake_worker(version, notes):
        called["worker"] = (version, notes)
        return {
            "published": True,
            "version": version,
            "publisher_authority": {
                "source_version": version,
                "staging": str(executor.staging),
            },
        }

    monkeypatch.setattr(executor, "_set_version", fake_set_version)
    monkeypatch.setattr(executor, "_publish_via_staging_worker", fake_worker)

    result = executor._publish("1.2.4", "test")
    assert result["published"] is True
    assert called == {
        "set_version": "1.2.4",
        "worker": ("1.2.4", "test"),
    }


def test_publish_requires_explicit_staging(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    _seed_runtime(executor.current, "1.2.3")

    with pytest.raises(RuntimeError, match="Staging is not initialized"):
        executor._publish("1.2.4", "test")


def test_worker_rejects_non_staging_module(tmp_path):
    staging = tmp_path / "Runtime" / "Staging"
    _seed_runtime(staging, "2.0.0")
    outside = tmp_path / "Current" / "src" / "ai_bridge" / "adapters" / "bridge_admin.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("", encoding="utf-8")

    with pytest.raises(publisher_worker.PublisherAuthorityError, match="not loaded from Staging"):
        publisher_worker._assert_source_authority(staging, outside)


def test_worker_executes_artifact_publish_with_staging_authority(tmp_path):
    root = tmp_path / "AI_Bridge"
    staging = root / "Runtime" / "Staging"
    module_file = staging / "src" / "ai_bridge" / "adapters" / "bridge_admin.py"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("", encoding="utf-8")
    _seed_runtime(staging, "2.0.1")

    class FakeExecutor:
        def __init__(self, *, data_dir):
            self.staging = staging
            self.data_dir = data_dir

        @staticmethod
        def _version(path):
            return "2.0.1"

        def _publish_artifacts_in_process(self, version, notes):
            return {"published": True, "version": version, "notes": notes}

    result = publisher_worker.run(
        {
            "root": str(root),
            "data_dir": str(tmp_path / "data"),
            "staging": str(staging),
            "version": "2.0.1",
            "notes": "self authority",
        },
        executor_factory=FakeExecutor,
        module_file=module_file,
    )
    assert result["published"] is True
    assert result["publisher_authority"]["mode"] == "staging_isolated_subprocess"
    assert result["publisher_authority"]["source_version"] == "2.0.1"


def test_staging_worker_subprocess_receives_staging_pythonpath(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    _seed_runtime(executor.current, "3.0.0")
    _seed_runtime(executor.staging, "3.0.1")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        request = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "published": True,
                "version": "3.0.1",
                "publisher_authority": {
                    "source_version": "3.0.1",
                    "staging": str(executor.staging),
                },
            }),
            stderr="",
        )

    monkeypatch.setattr("ai_bridge.adapters.bridge_admin.subprocess.run", fake_run)
    result = executor._publish_via_staging_worker("3.0.1", "notes")

    assert result["published"] is True
    assert captured["command"][-1] == "ai_bridge.deployment.publisher_worker"
    assert captured["kwargs"]["cwd"] == str(executor.staging)
    assert captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(
        (executor.staging / "src").resolve()
    )
