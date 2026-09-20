from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supervised_launcher_forwards_bridge_admin_kwargs():
    text = (ROOT / "launch_bridge.py").read_text(encoding="utf-8")
    assert "def _versioned_bridge_admin_init(self, *, data_dir: Path, **kwargs)" in text
    assert "_original_bridge_admin_init(self, data_dir=data_dir, **kwargs)" in text


def test_launch_bridge_process_stays_alive_on_isolated_port(tmp_path):
    runtime_root = tmp_path / "root"
    runtime_root.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["AI_BRIDGE_DATA_DIR"] = str(tmp_path / "data")
    env["AI_BRIDGE_ROOT"] = str(runtime_root)
    env["AI_BRIDGE_PORT"] = "18871"
    env["AI_BRIDGE_SUPERVISED"] = "1"
    env["AI_BRIDGE_RUNTIME_DIR"] = str(ROOT)

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "launch_bridge.py")],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.5)
        code = process.poll()
        if code is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(
                f"launch_bridge exited during startup ({code})\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
