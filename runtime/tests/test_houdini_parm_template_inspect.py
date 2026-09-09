from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import inspect_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.compat_ops import ARGUMENT_SCHEMAS


class FakeEnum:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeParmTemplate:
    def name(self):
        return "execute"

    def label(self):
        return "Execute"

    def type(self):
        return FakeEnum("Button")

    def scriptCallback(self):
        return 'hou.node("../FBX_OUT").parm("execute").pressButton()'

    def scriptCallbackLanguage(self):
        return FakeEnum("Python")

    def tags(self):
        return {"script_callback": "1"}

    def conditionals(self):
        return {FakeEnum("DisableWhen"): "{ enabled == 0 }"}

    def defaultValue(self):
        return ()

    def defaultExpression(self):
        return ()

    def defaultExpressionLanguage(self):
        return ()

    def disableWhen(self):
        return "{ enabled == 0 }"

    def hideWhen(self):
        return ""

    def menuItems(self):
        return ()

    def menuLabels(self):
        return ()

    def itemGeneratorScript(self):
        return ""

    def itemGeneratorScriptLanguage(self):
        return FakeEnum("Python")


class FakeParm:
    def parmTemplate(self):
        return FakeParmTemplate()

    def name(self):
        return "execute"

    def isSpare(self):
        return True


class FakeNode:
    def path(self):
        return "/obj/geo1/START"

    def parm(self, name):
        return FakeParm() if name == "execute" else None


class FakeHou:
    def node(self, path):
        return FakeNode() if path == "/obj/geo1/START" else None


def test_parm_template_inspection_returns_callback_without_execution():
    result = inspect_ops.inspect_parm_template(
        FakeHou(),
        "/obj/geo1/START",
        "execute",
    )

    assert result["path"] == "/obj/geo1/START"
    assert result["parameter"] == "execute"
    assert result["type"] == "Button"
    assert result["is_spare"] is True
    assert "pressButton()" in result["script_callback"]
    assert result["script_callback_language"] == "Python"
    assert result["callback_script"] == result["script_callback"]
    assert result["callback_language"] == result["script_callback_language"]
    assert result["disable_when"] == "{ enabled == 0 }"
    assert result["tags"] == {"script_callback": "1"}


def test_parm_template_inspection_is_registered_l1_read_only():
    by_name = {item["name"]: item for item in CAPABILITIES}
    cap = by_name["inspect.parm_template"]
    assert cap["write"] is False
    assert cap["risk"] == "L1"
    assert cap["rollback"] is False
    assert ARGUMENT_SCHEMAS["inspect.parm_template"]["write"] is False
