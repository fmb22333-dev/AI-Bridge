from __future__ import annotations
import json
from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def capture(self, command, result, *, checkpoint_id: str | None = None) -> Path | None:
        payload = result.result
        if checkpoint_id is None:
            if not isinstance(payload, dict) or "before" not in payload or "after" not in payload:
                return None
        data = {
            "command_id": command.command_id,
            "operation": command.operation,
            "arguments": command.arguments,
            "before": payload.get("before") if isinstance(payload, dict) else None,
            "after": payload.get("after") if isinstance(payload, dict) else None,
            "checkpoint_id": checkpoint_id,
            "status": result.status.value,
        }
        path = self.root / f"{command.command_id.replace(':','_')}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=repr), encoding="utf-8")
        return path

    def read(self, command_id: str) -> dict:
        path = self.root / f"{command_id.replace(':','_')}.json"
        return json.loads(path.read_text(encoding="utf-8"))
