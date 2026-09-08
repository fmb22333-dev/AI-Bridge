from __future__ import annotations
import json
from pathlib import Path


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, command, result) -> str:
        evidence_id = "ev_" + command.command_id.replace("/", "_").replace(":", "_")
        path = self.root / f"{evidence_id}.json"
        data = {
            "command": command.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return evidence_id

    def read(self, evidence_id: str) -> dict:
        path = self.root / f"{evidence_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))
