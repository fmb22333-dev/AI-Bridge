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
    def __init__(self):
        self._path = "E:/AA/TestCompat.hip"
        self.saved = None

    def path(self):
        return self._path

    def hasUnsavedChanges(self):
        return True

    def isLoadingHipFile(self):
        return False

    def save(self, file_name=None):
        if file_name:
            self._path = file_name
        self.saved = self._path


class FakeType:
    def nameWithCategory(self):
        return "Sop/null"


class FakeColor:
    def __init__(self, rgb=(0.1, 0.2, 0.3)):
        self._rgb = tuple(rgb)

    def rgb(self):
        return self._rgb


class FakeNode:
    def __init__(self, path):
        self._path = path
        self._name = path.rsplit("/", 1)[-1]
        self._selected = False
        self._current = False
        self._bypass = False
        self._display = True
        self._render = True
        self._template = False
        self._selectable = True
        self._position = (1.0, 2.0)
        self._color = FakeColor()
        self._comment = ""
        self._inputs = []
        self._outputs = []

    def path(self): return self._path
    def name(self): return self._name
    def type(self): return FakeType()
    def isBypassed(self): return self._bypass
    def isDisplayFlagSet(self): return self._display
    def isRenderFlagSet(self): return self._render
    def isTemplateFlagSet(self): return self._template
    def isSelectableInViewport(self): return self._selectable
    def comment(self): return self._comment
    def position(self): return self._position
    def color(self): return self._color
    def inputs(self): return self._inputs
    def outputs(self): return self._outputs
    def errors(self): return ()
    def warnings(self): return ()

    def bypass(self, value): self._bypass = bool(value)
    def setDisplayFlag(self, value): self._display = bool(value)
    def setRenderFlag(self, value): self._render = bool(value)
    def setTemplateFlag(self, value): self._template = bool(value)
    def setSelectableInViewport(self, value): self._selectable = bool(value)
    def setPosition(self, value): self._position = tuple(value)
    def setColor(self, value): self._color = value
    def setComment(self, value): self._comment = value
    def setName(self, value, unique_name=True):
        self._name = value
        parent = self._path.rsplit("/", 1)[0]
        self._path = parent + "/" + value
    def setSelected(self, value, clear_all_selected=False): self._selected = bool(value)
    def setCurrent(self, value, clear_all_selected=False): self._current = bool(value)


class FakeHou:
    Color = FakeColor

    def __init__(self):
        self.hipFile = FakeHipFile()
        self.nodes = {
            "/obj/a": FakeNode("/obj/a"),
            "/obj/b": FakeNode("/obj/b"),
        }

    def applicationVersionString(self): return "21.0.440"
    def frame(self): return 12.0
    def fps(self): return 24.0
    def node(self, path): return self.nodes.get(path)
    def selectedNodes(self): return [n for n in self.nodes.values() if n._selected]
    def clearAllSelected(self):
        for n in self.nodes.values():
            n._selected = False


def test_registered_houdini_compat_capabilities():
    names = {item["name"] for item in CAPABILITIES}
    expected = {
        "adapter.capabilities",
        "hip.status",
        "hip.save",
        "selection.get",
        "selection.set",
        "node.state",
        "node.set_state",
        "parm.batch_read",
        "parm.batch_write",
    }
    assert expected <= names


def test_adapter_capabilities_and_hip_status():
    hou = FakeHou()
    session = {
        "session_id": "HOU-TEST",
        "adapter_version": "0.2.0",
        "capabilities": CAPABILITIES,
    }
    caps = compat_ops.adapter_capabilities(hou, session)
    assert caps["adapter"] == "houdini"
    assert caps["host_version"] == "21.0.440"
    assert caps["session_id"] == "HOU-TEST"
    assert len(caps["capabilities"]) >= 9

    status = compat_ops.hip_status(hou)
    assert status["path"] == "E:/AA/TestCompat.hip"
    assert status["has_unsaved_changes"] is True
    assert status["frame"] == 12.0


def test_selection_and_node_state_roundtrip():
    hou = FakeHou()
    out = compat_ops.selection_set(hou, ["/obj/a"], current="/obj/a")
    assert out == {"paths": ["/obj/a"]}

    state = compat_ops.node_set_state(
        hou,
        "/obj/a",
        {
            "bypass": True,
            "display": False,
            "render": False,
            "template": True,
            "selectable": False,
            "position": [4.0, 5.0],
            "color": [0.8, 0.7, 0.6],
            "comment": "bridge",
        },
    )
    assert state["bypass"] is True
    assert state["display"] is False
    assert state["render"] is False
    assert state["template"] is True
    assert state["selectable"] is False
    assert state["position"] == [4.0, 5.0]
    assert state["color"] == [0.8, 0.7, 0.6]
    assert state["comment"] == "bridge"


def test_dispatcher_routes_new_operations():
    text = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    for name in (
        "adapter.capabilities",
        "hip.status",
        "hip.save",
        "selection.get",
        "selection.set",
        "node.state",
        "node.set_state",
        "parm.batch_read",
        "parm.batch_write",
    ):
        assert f'op == "{name}"' in text
