from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("AI_BRIDGE_DATA_DIR")
    return Path(override).expanduser() if override else Path.home() / ".ai_bridge"


def ensure_connection_config(data_dir: Path, *, url: str) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "connection.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        token = data.get("token") or secrets.token_urlsafe(32)
    else:
        token = secrets.token_urlsafe(32)
    data = {"url": url.rstrip("/"), "token": token}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return data


def load_workspaces(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_workspaces(path: Path, workspaces) -> None:
    data = [{"workspace_id": w.workspace_id, "root": str(w.root)} for w in workspaces]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
