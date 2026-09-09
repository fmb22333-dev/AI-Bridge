from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "houdini_adapter" / "python"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from ai_bridge_houdini import geometry_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.compat_ops import ARGUMENT_SCHEMAS


class FakeAttrib:
    def __init__(self, name, size=1, data_type="Float"):
        self._name = name
        self._size = size
        self._type = data_type

    def name(self):
        return self._name

    def size(self):
        return self._size

    def dataType(self):
        return self._type

    def isArrayType(self):
        return False


class FakeElement:
    def __init__(self, number, values):
        self._number = number
        self._values = dict(values)

    def number(self):
        return self._number

    def attribValue(self, name):
        return self._values[name]


class FakeVertex(FakeElement):
    def linearNumber(self):
        return self._number


class FakePrim(FakeElement):
    def __init__(self, number, values, vertices=None):
        super().__init__(number, values)
        self._vertices = list(vertices or [])

    def vertices(self):
        return list(self._vertices)


class FakeGeometry:
    def __init__(self, frame):
        self.frame = frame
        self._points = [
            FakeElement(0, {"id": 10, "P": [frame, 0.0, 0.0], "group": "A"}),
            FakeElement(1, {"id": 20, "P": [frame + 1.0, 2.0, 3.0], "group": "B"}),
            FakeElement(2, {"id": 20, "P": [frame + 2.0, 4.0, 6.0], "group": "B"}),
        ]
        self._vertices = [
            FakeVertex(0, {"uv": [0.0, 0.0, 0.0]}),
            FakeVertex(1, {"uv": [1.0, 0.0, 0.0]}),
        ]
        self._prims = [
            FakePrim(0, {"name": "piece"}, vertices=self._vertices),
        ]

    def pointAttribs(self):
        return [FakeAttrib("id", data_type="Int"), FakeAttrib("P", size=3), FakeAttrib("group", data_type="String")]

    def primAttribs(self):
        return [FakeAttrib("name", data_type="String")]

    def vertexAttribs(self):
        return [FakeAttrib("uv", size=3)]

    def globalAttribs(self):
        return []

    def points(self):
        return list(self._points)

    def prims(self):
        return list(self._prims)


class FakeNode:
    def __init__(self):
        self.frames = []
        self.current_calls = 0

    def path(self):
        return "/obj/geo1/OUT"

    def geometry(self):
        self.current_calls += 1
        return FakeGeometry(99.0)

    def geometryAtFrame(self, frame):
        self.frames.append(frame)
        return FakeGeometry(float(frame))


class FakeHou:
    def __init__(self, node):
        self._node = node

    def node(self, path):
        return self._node if path == self._node.path() else None


def test_geometry_query_multiframe_uses_geometry_at_frame_without_current_geometry():
    node = FakeNode()
    result = geometry_ops.query(
        FakeHou(node),
        node.path(),
        owner="point",
        attributes=["id", "P"],
        key_attribute="id",
        key_values=[20],
        mode="rows",
        frames=[1, 5],
        max_rows=10,
    )

    assert result["sampling"] == "geometryAtFrame"
    assert result["frame_count"] == 2
    assert node.frames == [1, 5]
    assert node.current_calls == 0
    assert [sample["selected_count"] for sample in result["samples"]] == [2, 2]
    assert result["samples"][0]["rows"][0]["attributes"]["id"] == 20
    assert result["samples"][1]["rows"][0]["attributes"]["P"][0] == 6.0


def test_geometry_query_stats_are_bounded_and_numeric_vector_aware():
    node = FakeNode()
    result = geometry_ops.query(
        FakeHou(node),
        node.path(),
        owner="point",
        attributes=["P"],
        mode="stats",
        frames=[2],
    )
    stats = result["samples"][0]["stats"]["P"]
    assert stats["kind"] == "numeric_vector"
    assert stats["size"] == 3
    assert stats["min"] == [2.0, 0.0, 0.0]
    assert stats["max"] == [4.0, 4.0, 6.0]


def test_vertex_query_flattens_primitive_vertices_without_geometry_itervertices():
    node = FakeNode()
    result = geometry_ops.query(
        FakeHou(node),
        node.path(),
        owner="vertex",
        attributes=["uv"],
        mode="rows",
        max_rows=10,
    )
    sample = result["samples"][0]
    assert sample["element_count"] == 2
    assert [row["id"] for row in sample["rows"]] == [0, 1]


def test_geometry_query_is_registered_read_only_l1_with_schema():
    cap = next(item for item in CAPABILITIES if item["name"] == "geometry.query")
    assert cap["write"] is False
    assert cap["risk"] == "L1"
    assert ARGUMENT_SCHEMAS["geometry.query"]["write"] is False
    assert "frames" in ARGUMENT_SCHEMAS["geometry.query"]["optional"]
