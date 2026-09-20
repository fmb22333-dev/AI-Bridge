from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def test_phase4_dispatcher_routes_batch_through_argument_normalizers():
    text = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    assert "normalize_parm_batch_read_args(args)" in text
    assert "normalize_parm_batch_write_args(args)" in text


def test_phase4_frozen_timeline_sidechain_stays_project_candidate():
    templates = {item["id"]: item for item in knowledge_registry.templates()}
    item = templates["retarget.frozen_timeline_sidechain"]
    assert item["state"] == "candidate"
    assert item["scope"] == "project_family"
    assert item["supported_host_versions"] == ["21.0.440"]
    assert len(item["examples"]) == 2
    assert item["roles"][0]["type"] == "Sop/timeshift"
    assert item["roles"][1]["type"] == "Sop/python"

    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}
    entry = entries["template.retarget.frozen_timeline_sidechain"]
    assert entry["state"] == "candidate"
    assert entry["scope"] == "project_family"
    assert entry["validation"]["regression_pass"] is True
    assert entry["validation"]["live_or_baseline_pass"] is True
