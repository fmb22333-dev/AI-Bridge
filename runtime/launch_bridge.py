from __future__ import annotations

import atexit
import json
import os
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import ai_bridge.adapters.bridge_admin as bridge_admin_module

_original_bridge_admin_init = bridge_admin_module.BridgeAdminExecutor.__init__


def _versioned_bridge_admin_init(self, *, data_dir: Path, **kwargs) -> None:
    _original_bridge_admin_init(self, data_dir=data_dir, **kwargs)
    runtime_env = os.environ.get("AI_BRIDGE_RUNTIME_DIR")
    if runtime_env:
        self.current = Path(runtime_env).resolve()


bridge_admin_module.BridgeAdminExecutor.__init__ = _versioned_bridge_admin_init

import ai_bridge.app as bridge_app_module

if os.environ.get("AI_BRIDGE_SUPERVISED") == "1":
    bridge_app_module.webbrowser.open = lambda *args, **kwargs: False

from ai_bridge.app import main
from ai_bridge.config import default_data_dir


def _target_for_data_dir(data_dir: Path) -> str:
    if not (data_dir / "remote.json").exists() or (data_dir / "remote.disabled").exists():
        return "/setup"
    return "/"


def _existing_core_control_url() -> str | None:
    data_dir = default_data_dir()
    config_path = data_dir / "connection.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        url = str(data["url"]).rstrip("/")
        token = str(data["token"])
        request = urllib.request.Request(
            url + "/health",
            headers={"Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(request, timeout=1.2) as response:
            if response.status != 200:
                return None
        target = _target_for_data_dir(data_dir)
        return f"{url}/control/bootstrap?token={token}&target={target}"
    except (OSError, ValueError, KeyError, urllib.error.URLError, TimeoutError):
        return None


def _register_pid_file() -> None:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_path = data_dir / "bridge.pid"
    pid = str(os.getpid())
    pid_path.write_text(pid, encoding="utf-8")

    def cleanup() -> None:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == pid:
                pid_path.unlink()
        except OSError:
            pass

    atexit.register(cleanup)


def run() -> None:
    existing = _existing_core_control_url()
    if existing:
        print("AI Bridge is already running.")
        if os.environ.get("AI_BRIDGE_SUPERVISED") != "1":
            webbrowser.open(existing)
        return
    _register_pid_file()
    main()


if __name__ == "__main__":
    run()
