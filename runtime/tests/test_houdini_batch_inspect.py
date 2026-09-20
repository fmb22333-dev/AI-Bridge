from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import inspect_ops
from ai_bridge_houdini.client import CAPABILITIES
from ai_bridge_houdini.compat_ops import ARGUMENT_SCHEMAS


def _fake_inspect(hou, path, mode="summary"):
    if path.endswith("/missing"):
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "type": {"name": "null"},
        "errors": [],
        "parameter_count": 3,
    }


def test_batch_nodes_mixed_success_is_partial_not_global_failure(monkeypatch):
    monkeypatch.setattr(inspect_ops, "inspect_node", _fake_inspect)
    result = inspect_ops.inspect_batch_nodes(
        object(),
        ["/obj/a", "/obj/missing", "/obj/b"],
        mode="summary",
        max_details=10,
    )
    assert result["requested"] == 3
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert [item["success"] for item in result["items"]] == [True, False, True]
    assert result["items"][1]["error"]["code"] == "NODE_NOT_FOUND"


def test_batch_nodes_max_details_keeps_all_status_rows(monkeypatch):
    monkeypatch.setattr(inspect_ops, "inspect_node", _fake_inspect)
    result = inspect_ops.inspect_batch_nodes(
        object(),
        ["/obj/a", "/obj/b", "/obj/c"],
        max_details=1,
    )
    assert result["requested"] == 3
    assert result["detailed"] == 1
    assert result["omitted"] == 2
    assert result["truncated"] is True
    assert len(result["items"]) == 3
    assert result["items"][0]["data"] is not None
    assert result["items"][1]["data"] is None
    assert result["items"][1]["detail_omitted"] is True


def test_batch_nodes_fields_filter(monkeypatch):
    monkeypatch.setattr(inspect_ops, "inspect_node", _fake_inspect)
    result = inspect_ops.inspect_batch_nodes(
        object(),
        ["/obj/a"],
        fields=["name", "type"],
    )
    assert result["items"][0]["data"] == {
        "name": "a",
        "type": {"name": "null"},
    }


def test_batch_nodes_rejects_invalid_input(monkeypatch):
    monkeypatch.setattr(inspect_ops, "inspect_node", _fake_inspect)
    with pytest.raises(ValueError, match="paths must be a non-empty list"):
        inspect_ops.inspect_batch_nodes(object(), [])
    with pytest.raises(ValueError, match="maximum batch size 1000"):
        inspect_ops.inspect_batch_nodes(object(), [f"/obj/n{i}" for i in range(1001)])
    with pytest.raises(ValueError, match="fields must be a list"):
        inspect_ops.inspect_batch_nodes(object(), ["/obj/a"], fields="name")


def test_batch_nodes_is_registered_and_self_describing():
    names = {item["name"] for item in CAPABILITIES}
    assert "inspect.batch_nodes" in names
    schema = ARGUMENT_SCHEMAS["inspect.batch_nodes"]
    assert schema["required"]["paths"] == "list[str] (1..1000)"
    assert "partial_failure" in schema

    dispatcher_text = (
        ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py"
    ).read_text(encoding="utf-8")
    assert 'op == "inspect.batch_nodes"' in dispatcher_text
