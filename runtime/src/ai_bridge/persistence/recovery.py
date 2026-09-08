from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, recovery_id: str, payload: dict) -> Path:
        data = dict(payload)
        data.setdefault("recovery_id", recovery_id)
        data.setdefault("recorded_at", utc_now_iso())
        path = self.root / f"{recovery_id}.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp.replace(path)
        return path

    def read(self, recovery_id: str) -> dict:
        return json.loads((self.root / f"{recovery_id}.json").read_text(encoding="utf-8"))

    def recent(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        items = []
        for path in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return items
