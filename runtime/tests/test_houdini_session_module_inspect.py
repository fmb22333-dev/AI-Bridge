from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

import ai_bridge_houdini
from ai_bridge_houdini import compat_ops, inspect_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.dispatcher import dispatch


SESSION_SOURCE = """VALUE = 1

def helper():
    return "helper"

@staticmethod
def run_handfix_batch(node):
    rop = node
    return rop

class BatchHelper:
    pass
"""


class FakeHipFile:
    def path(self):
        return "E:/Generic/Project.hip"


class FakeHou:
    hipFile = FakeHipFile()

    def __init__(self):
        self.source_reads = 0

    def sessionModuleSource(self):
        self.source_reads += 1
        return SESSION_SOURCE

    def applicationVersionString(self):
        return "21.0.440"


def test_session_module_symbol_read_is_bounded_ast_introspection():
    hou = FakeHou()
    result = inspect_ops.inspect_session_module(
        hou,
        symbol="run_handfix_batch",
        max_chars=10000,
    )

    assert hou.source_reads == 1
    assert result["scope"] == "symbol"
    assert result["symbol"] == "run_handfix_batch"
    assert result["truncated"] is False
    assert result["source"].startswith("@staticmethod\ndef run_handfix_batch")
    assert "def helper" not in result["source"]
    assert "class BatchHelper" not in result["source"]
    assert result["module_chars"] == len(SESSION_SOURCE)
    assert len(result["module_sha256"]) == 64
    assert len(result["source_sha256"]) == 64


def test_session_module_read_supports_bounded_full_module_output():
    result = inspect_ops.inspect_session_module(FakeHou(), max_chars=12)
    assert result["scope"] == "module"
    assert result["returned_chars"] == 12
    assert result["truncated"] is True
    assert result["source"] == SESSION_SOURCE[:12]


def test_session_module_missing_symbol_fails_closed():
    try:
        inspect_ops.inspect_session_module(FakeHou(), symbol="missing")
    except ValueError as exc:
        assert str(exc) == "SESSION_SYMBOL_NOT_FOUND: missing"
    else:
        raise AssertionError("missing symbol must fail")


def test_session_module_dispatch_is_read_only_and_registered():
    by_name = {item["name"]: item for item in CAPABILITIES}
    cap = by_name["inspect.session_module"]
    assert cap["write"] is False
    assert cap["risk"] == "L1"
    assert cap["rollback"] is False
    assert compat_ops.ARGUMENT_SCHEMAS["inspect.session_module"]["write"] is False
    assert ai_bridge_houdini.__version__ == "0.5.26"

    hou = FakeHou()
    out = dispatch(
        hou,
        {
            "command_id": "cmd-session-source",
            "operation": "inspect.session_module",
            "arguments": {"symbol": "run_handfix_batch"},
        },
        {
            "session_id": "HOU-TEST",
            "adapter_version": ai_bridge_houdini.__version__,
            "capabilities": CAPABILITIES,
        },
    )
    assert out["status"] == "success"
    assert out["stages"]["READBACK"] == "VERIFIED"
    assert "run_handfix_batch" in out["result"]["source"]


def test_capability_search_finds_session_module_source_introspection():
    hou = FakeHou()
    result = compat_ops.capability_search(
        hou,
        {
            "session_id": "HOU-SEARCH",
            "adapter_version": ai_bridge_houdini.__version__,
            "capabilities": CAPABILITIES,
        },
        "session module source callback",
        limit=20,
    )
    names = [item["name"] for item in result["results"]]
    assert "inspect.session_module" in names
    assert result["integrity"]["ok"] is True
