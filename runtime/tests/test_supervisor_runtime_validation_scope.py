from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_supervisor():
    root = Path(__file__).resolve().parents[2]
    path = root / "bootstrap" / "supervisor" / "0.1.6" / "_System" / "supervisor.py"
    spec = importlib.util.spec_from_file_location("ai_bridge_supervisor_scope_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_validation_scope_excludes_supervisor_tests(tmp_path):
    supervisor = _load_supervisor()
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_runtime_a.py", "test_transport_b.py", "test_supervisor_transport_watchdog.py", "test_supervisor_other.py"):
        (tests / name).write_text("", encoding="utf-8")

    selected = [Path(path).name for path in supervisor._runtime_validation_test_paths(tmp_path)]
    assert selected == ["test_runtime_a.py", "test_transport_b.py"]
