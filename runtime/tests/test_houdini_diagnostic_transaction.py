from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "houdini_adapter" / "python"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from ai_bridge_houdini import diagnostic_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.compat_ops import ARGUMENT_SCHEMAS


class FakeNode:
    def __init__(self, hou, path, parent=None):
        self.hou = hou
        self._path = path
        self.parent = parent
        self.user_data = {}
        self.destroyed = False

    def path(self):
        return self._path

    def name(self):
        return self._path.rsplit("/", 1)[-1]

    def setUserData(self, key, value):
        self.user_data[key] = value

    def userData(self, key):
        return self.user_data.get(key)

    def destroy(self):
        self.destroyed = True
        self.hou.registry.pop(self._path, None)


class FakeParent(FakeNode):
    def node(self, name):
        return self.hou.registry.get(self._path.rstrip("/") + "/" + name)

    def children(self):
        prefix = self._path.rstrip("/") + "/"
        return [
            node for path, node in list(self.hou.registry.items())
            if path.startswith(prefix) and "/" not in path[len(prefix):]
        ]


class FakeHou:
    def __init__(self):
        self.registry = {}
        self.parent = FakeParent(self, "/obj/geo1")
        self.source = FakeNode(self, "/obj/source")
        self.registry[self.parent.path()] = self.parent
        self.registry[self.source.path()] = self.source

    def node(self, path):
        return self.registry.get(path)


def fake_apply_factory(hou, created_objects):
    def fake_apply(_hou, parent_path, nodes, connections, layout=False, allow_update_existing=False):
        assert parent_path == "/obj/geo1"
        assert allow_update_existing is False
        refs = {}
        created = []
        for spec in nodes:
            path = parent_path + "/" + spec["name"]
            node = FakeNode(hou, path, hou.parent)
            hou.registry[path] = node
            created_objects.append(node)
            created.append(path)
            refs[spec["id"]] = path
        return {
            "parent": parent_path,
            "created": created,
            "updated": [],
            "connections": list(connections),
            "refs": refs,
            "verified": True,
        }
    return fake_apply


def test_diagnostic_transaction_success_collects_and_always_cleans(monkeypatch):
    hou = FakeHou()
    created_objects = []
    monkeypatch.setattr(diagnostic_ops.graph_ops, "apply_spec", fake_apply_factory(hou, created_objects))
    monkeypatch.setattr(
        diagnostic_ops.cook_ops,
        "cook",
        lambda hou, path, force=True: {
            "path": path,
            "errors": [],
            "warnings": [],
            "messages": [],
            "cook_status": "PASS",
        },
    )
    monkeypatch.setattr(
        diagnostic_ops.geometry_ops,
        "query",
        lambda hou, path, **kwargs: {"path": path, "mode": kwargs["mode"], "samples": []},
    )

    result = diagnostic_ops.transaction(
        hou,
        "/obj/geo1",
        diagnostic_id="cmd-diag-001",
        nodes=[
            {"id": "probe", "type": "null", "name": "probe"},
            {"id": "out", "type": "null", "name": "out"},
        ],
        connections=[
            {"source": "/obj/source", "target": "probe", "input": 0},
            {"source": "probe", "target": "out", "input": 0},
        ],
        cook_ref="out",
        collect=[{"ref": "out", "mode": "summary"}],
    )

    assert result["verified"] is True
    assert result["cleanup"]["verified"] is True
    assert len(result["cleanup"]["destroyed"]) == 2
    assert result["collections"][0]["result"]["mode"] == "summary"
    assert all(node.user_data["ai_bridge_diagnostic_id"] == "cmd-diag-001" for node in created_objects)
    assert not [p for p in hou.registry if "__AI_DIAG_" in p]


def test_diagnostic_transaction_cook_failure_preserves_failure_and_cleans(monkeypatch):
    hou = FakeHou()
    created_objects = []
    monkeypatch.setattr(diagnostic_ops.graph_ops, "apply_spec", fake_apply_factory(hou, created_objects))
    monkeypatch.setattr(
        diagnostic_ops.cook_ops,
        "cook",
        lambda hou, path, force=True: {
            "path": path,
            "errors": ["compile error"],
            "warnings": [],
            "messages": [],
            "cook_status": "FAILED",
        },
    )

    result = diagnostic_ops.transaction(
        hou,
        "/obj/geo1",
        diagnostic_id="cmd-diag-fail",
        nodes=[{"id": "out", "type": "null"}],
        cook_ref="out",
    )

    assert result["verified"] is False
    assert result["failure"]["code"] == "DIAGNOSTIC_COOK_FAILED"
    assert result["failure"]["host_errors"] == ["compile error"]
    assert result["cleanup"]["verified"] is True
    assert not [p for p in hou.registry if "__AI_DIAG_" in p]


def test_diagnostic_transaction_forbids_existing_node_as_connection_target(monkeypatch):
    hou = FakeHou()
    called = {"apply": False}

    def forbidden(*args, **kwargs):
        called["apply"] = True
        raise AssertionError("apply_spec must not run")

    monkeypatch.setattr(diagnostic_ops.graph_ops, "apply_spec", forbidden)

    with pytest.raises(ValueError, match="DIAGNOSTIC_EXTERNAL_TARGET_FORBIDDEN"):
        diagnostic_ops.transaction(
            hou,
            "/obj/geo1",
            diagnostic_id="cmd-diag-safe",
            nodes=[{"id": "probe", "type": "null"}],
            connections=[{"source": "probe", "target": "/obj/source"}],
            cook_ref="probe",
        )

    assert called["apply"] is False


def test_diagnostic_cleanup_mode_deletes_only_matching_userdata_marker():
    hou = FakeHou()
    marked = FakeNode(hou, "/obj/geo1/__AI_DIAG_old_probe", hou.parent)
    marked.setUserData("ai_bridge_diagnostic_id", "old-command")
    other = FakeNode(hou, "/obj/geo1/__AI_DIAG_other_probe", hou.parent)
    other.setUserData("ai_bridge_diagnostic_id", "different-command")
    hou.registry[marked.path()] = marked
    hou.registry[other.path()] = other

    result = diagnostic_ops.transaction(
        hou,
        "/obj/geo1",
        diagnostic_id="old-command",
        mode="cleanup",
    )

    assert result["verified"] is True
    assert result["matched"] == [marked.path()]
    assert hou.node(marked.path()) is None
    assert hou.node(other.path()) is other


def test_diagnostic_transaction_is_registered_recoverable_l2():
    cap = next(item for item in CAPABILITIES if item["name"] == "diagnostic.transaction")
    assert cap["write"] is True
    assert cap["risk"] == "L2"
    assert cap["rollback"] is True
    assert cap["rollback_on_failure"] is True
    schema = ARGUMENT_SCHEMAS["diagnostic.transaction"]
    assert "mode=cleanup" in schema["recovery"]
    assert "external targets are forbidden" in schema["safety"]
