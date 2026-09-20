from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import graph_ops, knowledge_registry, node_ops


class FakeCreated:
    def __init__(self, path):
        self._path = path
        self.destroyed = False

    def path(self):
        return self._path

    def destroy(self):
        self.destroyed = True


class FakeHou:
    def __init__(self, nodes=None):
        self.nodes = {node.path(): node for node in (nodes or [])}

    def node(self, path):
        node = self.nodes.get(path)
        if node is not None and getattr(node, "destroyed", False):
            return None
        return node


def _nodes():
    return [
        {"id": "import", "name": "IMPORT_TEST", "type": "kinefx::fbxcharacterimport"},
        {"id": "frame_info", "name": "FRAMEINFO_TEST", "type": "null"},
    ]


def _connections(h0="h0", h1="h1", h2="h2"):
    return [
        {"source": "import", "output": 2, "target": "frame_info", "input": 0},
        {"source": "import", "output": 0, "target": "/obj/inputFix", "input": 0, "expected_hash": h0},
        {"source": "import", "output": 1, "target": "/obj/inputFix", "input": 1, "expected_hash": h1},
        {"source": "frame_info", "output": 0, "target": "/obj/inputFix", "input": 2, "expected_hash": h2},
    ]


def test_phase5c_transactional_preflight_conflict_creates_nothing(monkeypatch):
    apply_calls = []

    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda hou, parent, nodes, connections: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "parent": parent,
        },
    )

    states = {
        0: {"target": "/obj/inputFix", "input_index": 0, "source": "/obj/old", "output_index": 0, "hash": "h0"},
        1: {"target": "/obj/inputFix", "input_index": 1, "source": "/obj/old", "output_index": 1, "hash": "actual-h1"},
        2: {"target": "/obj/inputFix", "input_index": 2, "source": "/obj/old2", "output_index": 0, "hash": "h2"},
    }
    monkeypatch.setattr(node_ops, "input_state", lambda hou, target, index: dict(states[int(index)]))
    monkeypatch.setattr(
        graph_ops,
        "apply_spec",
        lambda *args, **kwargs: apply_calls.append(True),
    )

    result = graph_ops.apply_transactional(
        object(),
        "/obj",
        _nodes(),
        _connections(),
        layout=False,
    )

    assert result["conflict"] is True
    assert result["verified"] is False
    assert result["created"] == []
    assert result["written_external_connections"] == 0
    assert apply_calls == []


def test_phase5c_transactional_batch_failure_deletes_created_nodes(monkeypatch):
    created_a = FakeCreated("/obj/IMPORT_TEST")
    created_b = FakeCreated("/obj/FRAMEINFO_TEST")
    hou = FakeHou([created_a, created_b])

    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda hou, parent, nodes, connections: {"ok": True, "errors": [], "warnings": []},
    )
    states = {
        0: {"target": "/obj/inputFix", "input_index": 0, "source": "/obj/old", "output_index": 0, "hash": "h0"},
        1: {"target": "/obj/inputFix", "input_index": 1, "source": "/obj/old", "output_index": 1, "hash": "h1"},
        2: {"target": "/obj/inputFix", "input_index": 2, "source": "/obj/old2", "output_index": 0, "hash": "h2"},
    }
    monkeypatch.setattr(node_ops, "input_state", lambda hou, target, index: dict(states[int(index)]))
    monkeypatch.setattr(
        graph_ops,
        "apply_spec",
        lambda *args, **kwargs: {
            "parent": "/obj",
            "created": [created_a.path(), created_b.path()],
            "updated": [],
            "connections": [{"source": created_a.path(), "target": created_b.path(), "input": 0, "output": 2}],
            "refs": {"import": created_a.path(), "frame_info": created_b.path()},
            "verified": True,
        },
    )

    captured = {}
    def fake_batch(hou, items):
        captured["items"] = items
        return {
            "conflict": True,
            "late_conflict": True,
            "written": 0,
            "verified": False,
            "rolled_back": True,
            "conflicts": [{"target": "/obj/inputFix", "input_index": 1}],
            "items": [],
        }

    monkeypatch.setattr(node_ops, "batch_connect", fake_batch)

    result = graph_ops.apply_transactional(
        hou,
        "/obj",
        _nodes(),
        _connections(),
        layout=False,
    )

    assert result["verified"] is False
    assert result["conflict"] is True
    assert result["cleanup"]["verified"] is True
    assert created_a.destroyed is True
    assert created_b.destroyed is True
    assert captured["items"][0]["source"] == created_a.path()
    assert captured["items"][2]["source"] == created_b.path()


def test_phase5c_transactional_success_resolves_refs_and_verifies(monkeypatch):
    created_a = FakeCreated("/obj/IMPORT_TEST")
    created_b = FakeCreated("/obj/FRAMEINFO_TEST")
    hou = FakeHou([created_a, created_b])

    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda hou, parent, nodes, connections: {"ok": True, "errors": [], "warnings": []},
    )
    states = {
        0: {"target": "/obj/inputFix", "input_index": 0, "source": "/obj/old", "output_index": 0, "hash": "h0"},
        1: {"target": "/obj/inputFix", "input_index": 1, "source": "/obj/old", "output_index": 1, "hash": "h1"},
        2: {"target": "/obj/inputFix", "input_index": 2, "source": "/obj/old2", "output_index": 0, "hash": "h2"},
    }
    monkeypatch.setattr(node_ops, "input_state", lambda hou, target, index: dict(states[int(index)]))
    monkeypatch.setattr(
        graph_ops,
        "apply_spec",
        lambda *args, **kwargs: {
            "parent": "/obj",
            "created": [created_a.path(), created_b.path()],
            "updated": [],
            "connections": [],
            "refs": {"import": created_a.path(), "frame_info": created_b.path()},
            "verified": True,
        },
    )

    captured = {}
    def fake_batch(hou, items):
        captured["items"] = items
        return {
            "conflict": False,
            "late_conflict": False,
            "written": 3,
            "verified": True,
            "rolled_back": False,
            "items": [{"verified": True}] * 3,
        }

    monkeypatch.setattr(node_ops, "batch_connect", fake_batch)

    result = graph_ops.apply_transactional(
        hou,
        "/obj",
        _nodes(),
        _connections(),
        layout=False,
    )

    assert result["conflict"] is False
    assert result["verified"] is True
    assert result["created"] == [created_a.path(), created_b.path()]
    assert result["written_external_connections"] == 3
    assert captured["items"] == [
        {
            "target": "/obj/inputFix",
            "input_index": 0,
            "source": created_a.path(),
            "output_index": 0,
            "expected_hash": "h0",
        },
        {
            "target": "/obj/inputFix",
            "input_index": 1,
            "source": created_a.path(),
            "output_index": 1,
            "expected_hash": "h1",
        },
        {
            "target": "/obj/inputFix",
            "input_index": 2,
            "source": created_b.path(),
            "output_index": 0,
            "expected_hash": "h2",
        },
    ]


def test_phase5c_transactional_requires_expected_hash_for_external_inputs(monkeypatch):
    monkeypatch.setattr(
        graph_ops,
        "validate_spec",
        lambda hou, parent, nodes, connections: {"ok": True, "errors": [], "warnings": []},
    )
    connections = _connections()
    connections[1].pop("expected_hash")

    result = graph_ops.apply_transactional(object(), "/obj", _nodes(), connections)

    assert result["verified"] is False
    assert result["conflict"] is False
    assert result["created"] == []
    assert result["errors"][0]["code"] == "EXPECTED_HASH_REQUIRED"


def test_phase5c_recipe_engine_treats_conflict_payload_as_failed_step(monkeypatch):
    recipe = {
        "id": "test.transactional",
        "version": "1.0",
        "steps": [
            {"op": "network.apply_transactional", "save_as": "apply", "arguments": {}}
        ],
    }
    monkeypatch.setattr(knowledge_registry, "get_recipe", lambda recipe_id: recipe)
    monkeypatch.setattr(
        knowledge_registry,
        "validate_recipe",
        lambda recipe_id, values, hou=None: {
            "ok": True,
            "errors": [],
            "resolved_steps": [],
            "preflight": None,
        },
    )
    monkeypatch.setattr(
        knowledge_registry,
        "_execute_primitive",
        lambda hou, op, args: {
            "conflict": True,
            "verified": False,
            "written_external_connections": 0,
        },
    )

    result = knowledge_registry.apply_recipe(object(), "test.transactional", {})

    assert result["verified"] is False
    assert result["failure"]["code"] == "RECIPE_STEP_FAILED"
    assert result["trace"][0]["status"] == "FAILED"


def test_phase5c_kernel_exposes_transactional_build_everywhere():
    dispatcher = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    registry = (ADAPTER_PY / "ai_bridge_houdini" / "knowledge_registry.py").read_text(encoding="utf-8")
    client = (ADAPTER_PY / "ai_bridge_houdini" / "client.py").read_text(encoding="utf-8")
    compat = (ADAPTER_PY / "ai_bridge_houdini" / "compat_ops.py").read_text(encoding="utf-8")

    assert 'op == "network.apply_transactional"' in dispatcher
    assert '"network.apply_transactional"' in registry
    assert '"network.apply_transactional"' in client
    assert '"network.apply_transactional"' in compat
