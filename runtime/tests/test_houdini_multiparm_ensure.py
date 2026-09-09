from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "houdini_adapter" / "python"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from ai_bridge_houdini import multiparm_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.compat_ops import ARGUMENT_SCHEMAS
from ai_bridge_houdini.hashing import stable_hash


class FakeParm:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    def eval(self):
        if self.name == "count":
            return self.owner.count
        return self.owner.values[self.name]

    def set(self, value):
        if self.name == "count":
            self.owner.count = int(value)
            self.owner.refresh()
            return
        self.owner.values[self.name] = value


class FakeNode:
    def __init__(self, count=3, resolve_child=True):
        self.count = count
        self.resolve_child = resolve_child
        self.values = {"name1": "a", "name2": "b", "name3": "c"}
        self.refresh()

    def path(self):
        return "/obj/geo1/raster"

    def refresh(self):
        if self.count >= 4 and self.resolve_child:
            self.values.setdefault("name4", "")
            self.values.setdefault("outtype4", 0)
        if self.count < 4:
            self.values.pop("name4", None)
            self.values.pop("outtype4", None)

    def parm(self, name):
        if name == "count":
            return FakeParm(self, name)
        if name in self.values:
            return FakeParm(self, name)
        return None


class FakeHou:
    def __init__(self, node):
        self._node = node

    def node(self, path):
        return self._node if path == self._node.path() else None


def test_multiparm_ensure_grows_resolves_children_writes_and_verifies():
    node = FakeNode(count=3)
    result = multiparm_ops.ensure(
        FakeHou(node),
        node.path(),
        "count",
        4,
        values={"name4": "N", "outtype4": 7},
    )

    assert result["verified"] is True
    assert result["count_changed"] is True
    assert result["after"]["count"] == 4
    assert node.count == 4
    assert node.values["name4"] == "N"
    assert node.values["outtype4"] == 7


def test_multiparm_ensure_is_idempotent_and_never_shrinks():
    node = FakeNode(count=5)
    node.values["name4"] = "N"
    result = multiparm_ops.ensure(
        FakeHou(node),
        node.path(),
        "count",
        4,
        values={"name4": "N"},
    )

    assert result["verified"] is True
    assert result["count_changed"] is False
    assert result["written"] == 0
    assert node.count == 5


def test_multiparm_missing_generated_child_rolls_count_back():
    node = FakeNode(count=3, resolve_child=False)
    result = multiparm_ops.ensure(
        FakeHou(node),
        node.path(),
        "count",
        4,
        values={"name4": "N"},
    )

    assert result["verified"] is False
    assert result["rolled_back"] is True
    assert result["rollback_verified"] is True
    assert result["failure"]["code"] == "MULTIPARM_ENSURE_FAILED"
    assert node.count == 3
    assert "name4" not in node.values


def test_multiparm_expected_count_hash_conflict_writes_nothing():
    node = FakeNode(count=3)
    result = multiparm_ops.ensure(
        FakeHou(node),
        node.path(),
        "count",
        4,
        values={"name4": "N"},
        expected_count_hash=stable_hash(99),
    )

    assert result["conflict"] is True
    assert result["written"] == 0
    assert node.count == 3


def test_multiparm_ensure_is_registered_l2_transactional_capability():
    cap = next(item for item in CAPABILITIES if item["name"] == "parm.multiparm.ensure")
    assert cap["write"] is True
    assert cap["risk"] == "L2"
    assert cap["rollback"] is True
    assert ARGUMENT_SCHEMAS["parm.multiparm.ensure"]["semantics"].startswith("grow-or-keep")
