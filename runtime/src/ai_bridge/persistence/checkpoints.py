from __future__ import annotations
import json
from pathlib import Path


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, checkpoint_id: str, metadata: dict) -> Path:
        path = self.root / f"{checkpoint_id}.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def read(self, checkpoint_id: str) -> dict:
        return json.loads((self.root / f"{checkpoint_id}.json").read_text(encoding="utf-8"))

    def recent(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        items = []
        for path in sorted(
            self.root.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return items
