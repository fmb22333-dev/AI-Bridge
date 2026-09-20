from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
TESTS = ROOT / "tests"
for path in (ADAPTER_PY, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_bridge_houdini import graph_ops
from test_houdini_p1_phase6a_network_ensure import FakeHou, add_node, ok_preflight


def _declaration(value=2):
    return [
        {
            "id": "target",
            "name": "TARGET",
            "type": "null",
            "parms": {"value": value},
        }
    ]


def test_phase6c_plan_hash_is_stable_and_independent_of_detail_limit(monkeypatch):
    hou = FakeHou()
    add_node(hou, "TARGET", "null", {"value": 1})
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    full = graph_ops.plan_ensure(
        hou, "/obj/geo1", _declaration(), [], max_details=100
    )
    compact = graph_ops.plan_ensure(
        hou, "/obj/geo1", _declaration(), [], max_details=0
    )

    assert full["ok"] is True
    assert len(full["plan_hash"]) == 64
    assert compact["plan_hash"] == full["plan_hash"]
    assert compact["detail_returned"] == 0
    assert compact["summary"] == full["summary"]


def test_phase6c_matching_plan_hash_allows_expected_reconciliation(monkeypatch):
    hou = FakeHou()
    target = add_node(hou, "TARGET", "null", {"value": 1})
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(hou, "/obj/geo1", _declaration(), [])
    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        _declaration(),
        [],
        expected_plan_hash=plan["plan_hash"],
    )

    assert result["verified"] is True
    assert result["conflict"] is False
    assert result["plan_guard"] == {
        "required": True,
        "matched": True,
        "expected_plan_hash": plan["plan_hash"],
        "actual_plan_hash": plan["plan_hash"],
    }
    assert target.parm("value").eval() == 2
    assert result["changed_parameters"] == 1


def test_phase6c_state_change_after_plan_conflicts_with_zero_ensure_write(monkeypatch):
    hou = FakeHou()
    target = add_node(hou, "TARGET", "null", {"value": 1})
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(hou, "/obj/geo1", _declaration(), [])
    target.parm("value").set(3)  # external change after Plan
    writes_before = target.parm("value").set_calls

    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        _declaration(),
        [],
        expected_plan_hash=plan["plan_hash"],
    )

    assert result["conflict"] is True
    assert result["verified"] is False
    assert result["written"] == 0
    assert result["errors"][0]["code"] == "PLAN_CONFLICT"
    assert result["plan_guard"]["required"] is True
    assert result["plan_guard"]["matched"] is False
    assert result["plan_guard"]["expected_plan_hash"] == plan["plan_hash"]
    assert result["plan_guard"]["actual_plan_hash"] != plan["plan_hash"]
    assert target.parm("value").eval() == 3
    assert target.parm("value").set_calls == writes_before
    assert hou.create_count == 0
    assert hou.connection_sets == 0


def test_phase6c_changed_declaration_rejects_old_plan_hash(monkeypatch):
    hou = FakeHou()
    target = add_node(hou, "TARGET", "null", {"value": 1})
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(hou, "/obj/geo1", _declaration(2), [])
    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        _declaration(4),
        [],
        expected_plan_hash=plan["plan_hash"],
    )

    assert result["conflict"] is True
    assert result["written"] == 0
    assert result["errors"][0]["code"] == "PLAN_CONFLICT"
    assert target.parm("value").eval() == 1
    assert hou.create_count == 0
    assert hou.connection_sets == 0


def test_phase6c_expected_plan_hash_is_exposed_without_new_primitive():
    dispatcher = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    compat = (ADAPTER_PY / "ai_bridge_houdini" / "compat_ops.py").read_text(encoding="utf-8")
    registry = (ADAPTER_PY / "ai_bridge_houdini" / "knowledge_registry.py").read_text(encoding="utf-8")
    client = (ADAPTER_PY / "ai_bridge_houdini" / "client.py").read_text(encoding="utf-8")

    assert 'expected_plan_hash=args.get("expected_plan_hash")' in dispatcher
    assert '"expected_plan_hash": "str from network.ensure_plan.plan_hash"' in compat
    assert 'expected_plan_hash=args.get("expected_plan_hash")' in registry
    assert '"network.ensure_plan"' in client
    assert '"network.ensure_transactional"' in client
