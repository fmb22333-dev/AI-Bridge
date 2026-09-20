from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import graph_ops, knowledge_registry


class FakeType:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def nameWithCategory(self):
        return self._name


class FakeParm:
    def __init__(self, value):
        self.value = value
        self.set_calls = 0

    def eval(self):
        return self.value

    def set(self, value):
        self.value = value
        self.set_calls += 1


class FakeConnection:
    def __init__(self, target, input_index, source, output_index):
        self.target = target
        self._input_index = input_index
        self.source = source
        self._output_index = output_index

    def inputIndex(self):
        return self._input_index

    def outputIndex(self):
        return self._output_index


class FakeNode:
    def __init__(self, hou, path, node_type, parms=None):
        self.hou = hou
        self._path = path
        self._type = FakeType(node_type)
        self._parms = {k: FakeParm(v) for k, v in (parms or {}).items()}
        self._inputs = {}
        self._output_indices = {}
        self.destroyed = False
        self._bypass = False
        self._display = False
        self._render = False
        self._template = False
        self._selectable = True
        self._comment = ""
        self._position = [0.0, 0.0]
        self.cook_errors = []
        self.cook_warnings = []
        self.cook_messages = []

    def path(self):
        return self._path

    def name(self):
        return self._path.rsplit("/", 1)[-1]

    def type(self):
        return self._type

    def parm(self, name):
        return self._parms.get(name)

    def input(self, index):
        return self._inputs.get(int(index))

    def inputConnections(self):
        out = []
        for index, source in self._inputs.items():
            if source is not None:
                out.append(FakeConnection(self, index, source, self._output_indices.get(index, 0)))
        return out

    def setInput(self, index, source, output_index=0):
        index = int(index)
        self._inputs[index] = source
        self._output_indices[index] = int(output_index)
        self.hou.connection_sets += 1

    def isBypassed(self):
        return self._bypass

    def bypass(self, value):
        self._bypass = bool(value)

    def isDisplayFlagSet(self):
        return self._display

    def setDisplayFlag(self, value):
        self._display = bool(value)

    def isRenderFlagSet(self):
        return self._render

    def setRenderFlag(self, value):
        self._render = bool(value)

    def isTemplateFlagSet(self):
        return self._template

    def setTemplateFlag(self, value):
        self._template = bool(value)

    def isSelectableInViewport(self):
        return self._selectable

    def setSelectableInViewport(self, value):
        self._selectable = bool(value)

    def comment(self):
        return self._comment

    def setComment(self, value):
        self._comment = str(value)

    def position(self):
        return list(self._position)

    def setPosition(self, value):
        self._position = list(value)

    def cook(self, force=True):
        return None

    def errors(self):
        return tuple(self.cook_errors)

    def warnings(self):
        return tuple(self.cook_warnings)

    def messages(self):
        return tuple(self.cook_messages)

    def destroy(self):
        self.destroyed = True


class FakeParent(FakeNode):
    def __init__(self, hou, path="/obj/geo1"):
        super().__init__(hou, path, "geo")
        self.children = {}

    def node(self, name):
        node = self.children.get(name)
        if node is not None and node.destroyed:
            return None
        return node

    def createNode(self, node_type, node_name=None, **kwargs):
        name = node_name or kwargs.get("name")
        if name in self.children and not self.children[name].destroyed:
            raise RuntimeError("duplicate node")
        node = FakeNode(self.hou, self._path + "/" + name, node_type)
        self.children[name] = node
        self.hou.nodes[node.path()] = node
        self.hou.create_count += 1
        return node

    def layoutChildren(self, nodes):
        return None


class FakeHou:
    def __init__(self):
        self.nodes = {}
        self.create_count = 0
        self.connection_sets = 0
        self.parent = FakeParent(self)
        self.nodes[self.parent.path()] = self.parent

    def node(self, path):
        node = self.nodes.get(path)
        if node is not None and node.destroyed:
            return None
        return node


def add_node(hou, name, node_type, parms=None):
    node = FakeNode(hou, hou.parent.path() + "/" + name, node_type, parms=parms)
    hou.parent.children[name] = node
    hou.nodes[node.path()] = node
    return node


def ok_preflight(parent="/obj/geo1"):
    return {"ok": True, "parent": parent, "refs": {}, "errors": [], "warnings": []}


def test_phase6a_existing_node_is_updated_without_duplicate_creation(monkeypatch):
    hou = FakeHou()
    node = add_node(hou, "FIX_HANDS", "attribwrangle", {"snippet": "old", "spare": 7})
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        nodes=[
            {
                "id": "fix",
                "name": "FIX_HANDS",
                "type": "attribwrangle",
                "parms": {"snippet": "new"},
                "state": {"comment": "managed"},
            }
        ],
        connections=[],
    )

    assert result["verified"] is True
    assert result["created"] == []
    assert result["updated"] == ["/obj/geo1/FIX_HANDS"]
    assert hou.create_count == 0
    assert node.parm("snippet").eval() == "new"
    assert node.parm("spare").eval() == 7
    assert node.comment() == "managed"


def test_phase6a_second_run_is_idempotent_and_skips_equal_writes(monkeypatch):
    hou = FakeHou()
    source = add_node(hou, "SOURCE", "null")
    target = add_node(hou, "TARGET", "null", {"label": "same"})
    target.setInput(0, source, 2)
    hou.connection_sets = 0
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    first_sets = target.parm("label").set_calls
    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        nodes=[
            {"id": "source", "name": "SOURCE", "type": "null"},
            {"id": "target", "name": "TARGET", "type": "null", "parms": {"label": "same"}},
        ],
        connections=[{"source": "source", "output": 2, "target": "target", "input": 0}],
    )

    assert result["verified"] is True
    assert result["created"] == []
    assert result["changed_parameters"] == 0
    assert result["changed_connections"] == 0
    assert target.parm("label").set_calls == first_sets
    assert hou.connection_sets == 0


def test_phase6a_cook_failure_rolls_back_existing_and_deletes_created(monkeypatch):
    hou = FakeHou()
    old_source = add_node(hou, "OLD", "null")
    target = add_node(hou, "TARGET", "null", {"value": 1})
    target.setInput(0, old_source, 0)
    target.setComment("before")
    target.cook_errors = ["compile failed"]
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        nodes=[
            {"id": "new", "name": "NEW", "type": "null"},
            {
                "id": "target",
                "name": "TARGET",
                "type": "null",
                "parms": {"value": 2},
                "state": {"comment": "after"},
            },
        ],
        connections=[{"source": "new", "output": 0, "target": "target", "input": 0}],
        cook_path="target",
    )

    assert result["verified"] is False
    assert result["rolled_back"] is True
    assert result["cook"]["cook_status"] == "FAILED"
    assert target.parm("value").eval() == 1
    assert target.comment() == "before"
    assert target.input(0) is old_source
    assert hou.node("/obj/geo1/NEW") is None


def test_phase6a_preflight_failure_performs_zero_mutation(monkeypatch):
    hou = FakeHou()
    node = add_node(hou, "TARGET", "null", {"value": 1})
    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda *a, **k: {
            "ok": False,
            "errors": [{"code": "NODE_TYPE_MISMATCH"}],
            "warnings": [],
        },
    )

    result = graph_ops.ensure_transactional(
        hou,
        "/obj/geo1",
        nodes=[{"id": "target", "name": "TARGET", "type": "attribwrangle", "parms": {"value": 2}}],
        connections=[],
    )

    assert result["verified"] is False
    assert result["written"] == 0
    assert hou.create_count == 0
    assert node.parm("value").eval() == 1


def test_phase6a_duplicate_target_input_is_rejected_before_write(monkeypatch):
    hou = FakeHou()
    a = add_node(hou, "A", "null")
    b = add_node(hou, "B", "null")
    target = add_node(hou, "TARGET", "null")
    monkeypatch.setattr(graph_ops, "validate_spec", lambda *a, **k: ok_preflight())

    result = graph_ops.ensure_transactional(
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

    assert result["verified"] is False
    assert result["errors"][0]["code"] == "DUPLICATE_TARGET_INPUT"
    assert hou.connection_sets == 0


def test_phase6a_ensure_and_cook_recipe_is_promoted_and_short():
    recipe = knowledge_registry.get_recipe("network.ensure_and_cook")
    assert recipe["promotion_state"] == "promoted"
    assert recipe["version"] == "1.1"
    assert recipe["scope"] == "houdini_declarative_network_21_0_440"
    assert recipe["required"] == ["parent", "nodes", "connections", "cook_path"]
    assert recipe["optional"] == ["expected_plan_hash"]
    assert recipe["compression"]["caller_operations"] == 1
    assert recipe["steps"] == [
        {
            "op": "network.ensure_transactional",
            "save_as": "ensure",
            "arguments": {
                "parent": {"$arg": "parent"},
                "nodes": {"$arg": "nodes"},
                "connections": {"$arg": "connections"},
                "cook_path": {"$arg": "cook_path"},
                "layout": False,
                "expected_plan_hash": {"$arg_optional": "expected_plan_hash"},
            },
        }
    ]


def test_phase6a_optional_plan_hash_resolves_without_forcing_callers_to_supply_it():
    base_values = {
        "parent": "/obj/geo1",
        "nodes": [],
        "connections": [],
        "cook_path": "/obj/geo1/TARGET",
    }
    plain = knowledge_registry.validate_recipe(
        "network.ensure_and_cook",
        base_values,
    )
    assert plain["ok"] is True
    assert plain["resolved_steps"][0]["arguments"]["expected_plan_hash"] is None

    guarded = knowledge_registry.validate_recipe(
        "network.ensure_and_cook",
        {**base_values, "expected_plan_hash": "plan-sha"},
    )
    assert guarded["ok"] is True
    assert guarded["resolved_steps"][0]["arguments"]["expected_plan_hash"] == "plan-sha"


def test_phase6a_kernel_is_exposed_to_dispatcher_capabilities_and_recipe_engine():
    dispatcher = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    client = (ADAPTER_PY / "ai_bridge_houdini" / "client.py").read_text(encoding="utf-8")
    registry = (ADAPTER_PY / "ai_bridge_houdini" / "knowledge_registry.py").read_text(encoding="utf-8")
    compat = (ADAPTER_PY / "ai_bridge_houdini" / "compat_ops.py").read_text(encoding="utf-8")

    assert 'op == "network.ensure_transactional"' in dispatcher
    assert '"name": "network.ensure_transactional"' in client
    assert 'if op == "network.ensure_transactional"' in registry
    assert "'network.ensure_transactional'" in registry
    assert '"network.ensure_transactional"' in compat



def test_phase6a_promotion_and_host_rule_preserve_authority_boundary():
    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}
    promoted = entries["recipe.network.ensure_and_cook"]
    assert promoted["state"] == "promoted"
    assert promoted["scope"] == "houdini_declarative_network_21_0_440"
    assert promoted["validation"]["regression_pass"] is True
    assert promoted["validation"]["live_or_baseline_pass"] is True

    rules = {item["id"]: item for item in knowledge_registry.host_rules()}
    rule = rules["network.ensure_reconciliation_vs_expected_hash"]
    assert rule["promotion_state"] == "promoted"
    assert "expected hashes" in rule["summary"]
    assert rule["assumptions"] == [
        "Ensure only owns fields and target inputs explicitly present in the declaration.",
        "Unspecified parameters, state fields, and inputs are preserved.",
    ]
