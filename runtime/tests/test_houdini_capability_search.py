from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import compat_ops
from ai_bridge_houdini.client import CAPABILITIES


class FakeHipFile:
    def path(self):
        return "E:/Generic/Project.hip"


class FakeHou:
    hipFile = FakeHipFile()

    def applicationVersionString(self):
        return "21.0.440"


def _session():
    return {
        "session_id": "HOU-SEARCH",
        "adapter_version": "0.5.22",
        "capabilities": CAPABILITIES,
    }


def test_capability_search_finds_reconciliation_before_low_level_composition():
    result = compat_ops.capability_search(
        FakeHou(),
        _session(),
        "network reconcile cook guarded",
        limit=20,
    )
    by_name = {item["name"]: item for item in result["results"]}

    assert "network.ensure_transactional" in by_name
    assert "network.ensure_and_cook" in by_name
    assert by_name["network.ensure_and_cook"]["promotion_state"] == "promoted"
    assert by_name["network.ensure_and_cook"]["execution_authorized"] is True
    assert result["integrity"]["ok"] is True
    assert result["integrity"]["issue_count"] == 0


def test_capability_search_exposes_only_distributable_network_recipe_authority():
    result = compat_ops.capability_search(
        FakeHou(),
        _session(),
        "network build cook",
        limit=50,
    )
    rows = {item["name"]: item for item in result["results"]}

    assert "network.build_and_cook" not in rows
    assert "network.build" not in rows
    current = rows["network.ensure_and_cook"]
    assert current["promotion_state"] == "promoted"
    assert current["execution_authorized"] is True


def test_capability_search_finds_callback_introspection():
    result = compat_ops.capability_search(
        FakeHou(),
        _session(),
        "callback parm template",
        limit=20,
    )
    names = [item["name"] for item in result["results"]]
    assert "inspect.parm_template" in names
