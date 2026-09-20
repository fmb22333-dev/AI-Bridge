from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters import bridge_admin


def make_executor(tmp_path: Path):
    executor = object.__new__(bridge_admin.BridgeAdminExecutor)
    executor.staging = tmp_path / "Staging"
    executor.current = tmp_path / "Current"
    executor.staging.mkdir(parents=True)
    executor.current.mkdir(parents=True)
    return executor


def test_devspeed_descriptor_exposes_batch_dev_operations():
    names = {cap.name for cap in bridge_admin.descriptor().capabilities}
    assert "bridge.update.read_files" in names
    assert "bridge.update.write_files" in names
    assert "bridge.update.test_subset" in names


def test_devspeed_write_files_preflights_entire_batch(tmp_path):
    executor = make_executor(tmp_path)
    target = executor.staging / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ValueError):
        executor._write_files([
            {"path": "src/a.py", "text": "new"},
            {"path": "../escape.py", "text": "bad"},
        ])

    assert target.read_text(encoding="utf-8") == "old"


def test_devspeed_write_files_writes_multiple_files_in_one_call(tmp_path):
    executor = make_executor(tmp_path)
    result = executor._write_files([
        {"path": "src/a.py", "text": "A\n"},
        {"path": "tests/test_b.py", "text": "B\n"},
    ])

    assert result["count"] == 2
    assert result["total_chars"] == 4
    assert (executor.staging / "src" / "a.py").read_text(encoding="utf-8") == "A\n"
    assert (executor.staging / "tests" / "test_b.py").read_text(encoding="utf-8") == "B\n"


def test_devspeed_read_files_batches_current_or_staging(tmp_path):
    executor = make_executor(tmp_path)
    (executor.current / "src").mkdir(parents=True)
    (executor.current / "src" / "a.py").write_text("current", encoding="utf-8")
    (executor.staging / "src").mkdir(parents=True)
    (executor.staging / "src" / "a.py").write_text("staging", encoding="utf-8")

    current = executor._read_files(["src/a.py"], source="current")
    staging = executor._read_files(["src/a.py"], source="staging")

    assert current["files"][0]["text"] == "current"
    assert staging["files"][0]["text"] == "staging"


def test_devspeed_subset_restricts_paths_to_tests(tmp_path, monkeypatch):
    executor = make_executor(tmp_path)
    tests = executor.staging / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fast.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr(bridge_admin.subprocess, "run", fake_run)

    result = executor._test_subset(["tests/test_fast.py"], k="test_ok")

    assert result["tests"] == "PASS"
    assert result["selected_paths"] == ["tests/test_fast.py"]
    assert "-k" in captured["command"]
    assert "test_ok" in captured["command"]

    with pytest.raises(ValueError):
        executor._test_subset(["src/ai_bridge/app.py"])


def test_devspeed_source_mirror_snapshot_is_directly_browsable(tmp_path):
    executor = make_executor(tmp_path)
    (executor.staging / "src" / "pkg").mkdir(parents=True)
    (executor.staging / "src" / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (executor.staging / "tests").mkdir(parents=True)
    (executor.staging / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    cache = executor.staging / "src" / "pkg" / "__pycache__"
    cache.mkdir()
    (cache / "a.pyc").write_bytes(b"ignore")

    snapshot = executor._source_mirror_snapshot("9.9.9")

    assert snapshot["runtime-src/src/pkg/a.py"] == "x = 1\n"
    assert snapshot["runtime-src/tests/test_a.py"] == "def test_a(): pass\n"
    assert "runtime-src/SOURCE_INDEX.json" in snapshot
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in snapshot)


def test_set_version_uses_pyproject_as_presence_version_authority(tmp_path):
    executor = make_executor(tmp_path)
    (executor.staging / "src" / "ai_bridge" / "transport").mkdir(parents=True)
    controller = executor.staging / "src" / "ai_bridge" / "transport" / "remote_controller.py"
    controller.write_text('bridge_version = runtime_version()\n', encoding="utf-8")
    (executor.staging / "src" / "ai_bridge" / "web" / "templates").mkdir(parents=True)
    dashboard = executor.staging / "src" / "ai_bridge" / "web" / "templates" / "index.html"
    dashboard.write_text('AI Bridge <small>V0.0.0</small>\n', encoding="utf-8")
    pyproject = executor.staging / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.0.0"\n', encoding="utf-8")

    executor._set_version("9.9.9")

    assert 'version = "9.9.9"' in pyproject.read_text(encoding="utf-8")
    assert controller.read_text(encoding="utf-8") == 'bridge_version = runtime_version()\n'
    assert 'AI Bridge <small>V9.9.9</small>' in dashboard.read_text(encoding="utf-8")
