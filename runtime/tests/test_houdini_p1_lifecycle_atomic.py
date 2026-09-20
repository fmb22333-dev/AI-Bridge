from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_p1_promotion_registry_uses_last_known_good_on_invalid_update(tmp_path, monkeypatch):
    path = tmp_path / "promotion_registry.json"
    valid = {
        "entries": [
            {
                "id": "candidate.test",
                "kind": "template",
                "state": "candidate",
                "scope": "test",
                "evidence": [{"source_class": "test", "summary": "valid"}],
            }
        ]
    }
    path.write_text(json.dumps(valid), encoding="utf-8")
    monkeypatch.setattr(knowledge_registry, "PROMOTION_REGISTRY", path)
    key = knowledge_registry._p0_path_key(path)

    try:
        first = knowledge_registry.promotion_entries()
        assert first[0]["id"] == "candidate.test"

        path.write_text(json.dumps({"entries": "not-a-list"}), encoding="utf-8")
        second = knowledge_registry.promotion_entries()

        assert second == first
        status = knowledge_registry.status()
        assert status["knowledge_degraded"] is True
        assert any(
            item["code"] == "KNOWLEDGE_VALIDATION_FAILED"
            and item["path"].endswith("promotion_registry.json")
            for item in status["validation_errors"]
        )
    finally:
        knowledge_registry._p0_validation_errors.pop(key, None)
        knowledge_registry._p0_json_last_good.pop(key, None)
