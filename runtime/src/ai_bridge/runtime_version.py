from __future__ import annotations

import tomllib
from pathlib import Path


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_version(root: Path | None = None) -> str:
    base = Path(root).resolve() if root is not None else runtime_root()
    path = base / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        version = str(data.get("project", {}).get("version") or "").strip()
    except Exception:
        version = ""
    return version or "unknown"
