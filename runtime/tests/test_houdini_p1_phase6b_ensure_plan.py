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


def test_phase6b_zero_change_plan_is_read_only_and_compact(monkeypatch):
    hou = FakeHou()
    source = add_node(hou, "SOURCE", "null")
    target = add_node(hou, "TARGET", "null", {"label": "same"})
    target.setInput(0, source, 2)
    hou.connection_sets = 0
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    before_sets = target.parm("label").set_calls
    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=[
            {"id": "source", "name": "SOURCE", "type": "null"},
            {"id": "target", "name": "TARGET", "type": "null", "parms": {"label": "same"}},
        ],
        connections=[{"source": "source", "output": 2, "target": "target", "input": 0}],
        cook_path="target",
    )

    assert plan["ok"] is True
    assert plan["read_only"] is True
    assert plan["summary"] == {
        "create_nodes": 0,
        "update_nodes": 0,
        "parameter_changes": 0,
        "state_changes": 0,
        "connection_changes": 0,
        "noop_nodes": 2,
        "noop_connections": 1,
        "noop_total": 3,
        "total_changes": 0,
        "would_cook": True,
    }
    assert plan["compact"] == "CREATE 0 / UPDATE_PARMS 0 / UPDATE_STATE 0 / CONNECT 0 / NOOP 3"
    assert plan["cook"] == {
        "requested": True,
        "target": "/obj/geo1/TARGET",
        "executed": False,
    }
    assert target.parm("label").set_calls == before_sets
    assert target.parm("label").eval() == "same"
    assert hou.connection_sets == 0
    assert hou.create_count == 0


def test_phase6b_plan_reports_existing_parameter_state_and_connection_diff_without_mutation(monkeypatch):
    hou = FakeHou()
    old_source = add_node(hou, "OLD", "null")
    new_source = add_node(hou, "NEW_SOURCE", "null")
    target = add_node(hou, "TARGET", "null", {"value": 1, "spare": 9})
    target.setInput(0, old_source, 0)
    target.setComment("before")
    hou.connection_sets = 0
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=[
            {"id": "new_source", "name": "NEW_SOURCE", "type": "null"},
            {
                "id": "target",
                "name": "TARGET",
                "type": "null",
                "parms": {"value": 2},
                "state": {"comment": "after"},
            },
        ],
        connections=[{"source": "new_source", "output": 3, "target": "target", "input": 0}],
    )

    assert plan["ok"] is True
    assert plan["summary"]["update_nodes"] == 1
    assert plan["summary"]["parameter_changes"] == 1
    assert plan["summary"]["state_changes"] == 1
    assert plan["summary"]["connection_changes"] == 1
    target_plan = next(item for item in plan["nodes"] if item["ref"] == "target")
    assert target_plan["action"] == "UPDATE"
    assert target_plan["parameter_changes"] == [
        {"parameter": "value", "before": 1, "after": 2}
    ]
    assert target_plan["state_changes"] == [
        {"state": "comment", "before": "before", "after": "after"}
    ]
    connection = plan["connections"][0]
    assert connection["action"] == "CONNECT"
    assert connection["before"] == {
        "source": "/obj/geo1/OLD",
        "output_index": 0,
    }
    assert connection["after"] == {
        "source": "/obj/geo1/NEW_SOURCE",
        "output_index": 3,
    }

    assert target.parm("value").eval() == 1
    assert target.parm("spare").eval() == 9
    assert target.comment() == "before"
    assert target.input(0) is old_source
    assert hou.connection_sets == 0
    assert hou.create_count == 0


def test_phase6b_plan_reports_missing_node_creation_without_probe_creation(monkeypatch):
    hou = FakeHou()
    existing = add_node(hou, "EXISTING", "null")
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=[
            {
                "id": "new",
                "name": "NEW_NODE",
                "type": "null",
                "parms": {"label": "hello"},
                "state": {"comment": "created by ensure"},
            },
            {"id": "existing", "name": "EXISTING", "type": "null"},
        ],
        connections=[{"source": "new", "output": 0, "target": "existing", "input": 0}],
        cook_path="new",
    )

    assert plan["ok"] is True
    assert plan["summary"]["create_nodes"] == 1
    assert plan["summary"]["connection_changes"] == 1
    created = next(item for item in plan["nodes"] if item["ref"] == "new")
    assert created == {
        "ref": "new",
        "path": "/obj/geo1/NEW_NODE",
        "type": "null",
        "exists": False,
        "action": "CREATE",
        "initial_parameters": {"label": "hello"},
        "initial_state": {"comment": "created by ensure"},
        "parameter_changes": [],
        "state_changes": [],
    }
    assert plan["cook"]["target"] == "/obj/geo1/NEW_NODE"
    assert hou.node("/obj/geo1/NEW_NODE") is None
    assert hou.create_count == 0
    assert hou.connection_sets == 0
    assert existing.input(0) is None


def test_phase6b_invalid_plan_fails_without_mutation(monkeypatch):
    hou = FakeHou()
    target = add_node(hou, "TARGET", "null")
    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda *a, **k: {
            "ok": False,
            "errors": [{"code": "NODE_TYPE_MISMATCH"}],
            "warnings": [],
        },
    )

    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=[{"id": "target", "name": "TARGET", "type": "attribwrangle"}],
        connections=[],
    )

    assert plan["ok"] is False
    assert plan["read_only"] is True
    assert plan["summary"]["total_changes"] == 0
    assert plan["errors"][0]["code"] == "NODE_TYPE_MISMATCH"
    assert hou.create_count == 0
    assert hou.connection_sets == 0
    assert hou.node("/obj/geo1/TARGET") is target


def test_phase6b_duplicate_target_input_is_rejected_in_plan_before_any_write(monkeypatch):
    hou = FakeHou()
    add_node(hou, "A", "null")
    add_node(hou, "B", "null")
    add_node(hou, "TARGET", "null")
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=[
            {"id": "a", "name": "A", "type": "null"},
            {"id": "b", "name": "B", "type": "null"},
            {"id": "target", "name": "TARGET", "type": "null"},
        ],
        connections=[
            {"source": "a", "target": "target", "input": 0, "output": 0},
            {"source": "b", "target": "target", "input": 0, "output": 0},
        ],
    )

    assert plan["ok"] is False
    assert plan["errors"][0]["code"] == "DUPLICATE_TARGET_INPUT"
    assert plan["summary"]["total_changes"] == 0
    assert hou.connection_sets == 0
    assert hou.create_count == 0


def test_phase6b_detail_limit_does_not_truncate_summary(monkeypatch):
    hou = FakeHou()
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    nodes = [
        {"id": f"n{i}", "name": f"N{i}", "type": "null"}
        for i in range(5)
    ]
    plan = graph_ops.plan_ensure(
        hou,
        "/obj/geo1",
        nodes=nodes,
        connections=[],
        max_details=2,
    )

    assert plan["ok"] is True
    assert plan["summary"]["create_nodes"] == 5
    assert plan["summary"]["total_changes"] == 5
    assert len(plan["nodes"]) == 2
    assert plan["truncated"] is True
    assert plan["detail_total"] == 5
    assert plan["detail_returned"] == 2
    assert hou.create_count == 0


def test_phase6b_plan_capability_is_read_only_and_self_describing():
    client = (ADAPTER_PY / "ai_bridge_houdini" / "client.py").read_text(encoding="utf-8")
    dispatcher = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    compat = (ADAPTER_PY / "ai_bridge_houdini" / "compat_ops.py").read_text(encoding="utf-8")
    registry = (ADAPTER_PY / "ai_bridge_houdini" / "knowledge_registry.py").read_text(encoding="utf-8")

    assert '"name": "network.ensure_plan"' in client
    assert '"write": False' in client
    assert 'op == "network.ensure_plan"' in dispatcher
    assert '"network.ensure_plan"' in compat
    assert 'if op == "network.ensure_plan"' in registry
    assert "'network.ensure_plan'" in registry



def test_phase6b_promoted_host_rule_records_advisory_not_lock_boundary():
    from ai_bridge_houdini import knowledge_registry

    rules = {item["id"]: item for item in knowledge_registry.host_rules()}
    rule = rules["network.ensure_plan_advisory_preview"]
    assert rule["promotion_state"] == "promoted"
    assert rule["validation_status"]["read_only"] is True
    assert rule["validation_status"]["live_probe_creation_absent"] is True
    assert "not a lock" in rule["summary"].lower()

    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}
    entry = entries["hostrule.network.ensure_plan_advisory_preview"]
    assert entry["state"] == "promoted"
    assert entry["scope"] == "houdini_declarative_network_21_0_440"
    assert entry["validation"]["regression_pass"] is True
    assert entry["validation"]["live_or_baseline_pass"] is True
