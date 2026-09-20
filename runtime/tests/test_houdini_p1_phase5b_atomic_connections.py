from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import node_ops


class FakeConnection:
    def __init__(self, input_index, output_index):
        self._input = input_index
        self._output = output_index

    def inputIndex(self):
        return self._input

    def outputIndex(self):
        return self._output


class FakeNode:
    def __init__(self, path):
        self._path = path
        self._inputs = {}
        self._fail_once_on = None

    def path(self):
        return self._path

    def input(self, index):
        value = self._inputs.get(int(index))
        return None if value is None else value[0]

    def inputConnections(self):
        return [
            FakeConnection(index, output)
            for index, (source, output) in sorted(self._inputs.items())
            if source is not None
        ]

    def setInput(self, index, source, output_index=0):
        index = int(index)
        if self._fail_once_on == index:
            self._fail_once_on = None
            raise RuntimeError("synthetic setInput failure")
        if source is None:
            self._inputs.pop(index, None)
        else:
            self._inputs[index] = (source, int(output_index))

    def fail_once_on(self, index):
        self._fail_once_on = int(index)


class FakeHou:
    def __init__(self, nodes):
        self.nodes = {node.path(): node for node in nodes}

    def node(self, path):
        return self.nodes.get(path)


def make_hou():
    src_a = FakeNode("/obj/src_a")
    src_b = FakeNode("/obj/src_b")
    target = FakeNode("/obj/target")
    return FakeHou([src_a, src_b, target]), src_a, src_b, target


def test_phase5b_input_state_hash_includes_source_output_index():
    hou, src_a, _, target = make_hou()
    target.setInput(0, src_a, 0)
    state0 = node_ops.input_state(hou, target.path(), 0)

    target.setInput(0, src_a, 1)
    state1 = node_ops.input_state(hou, target.path(), 0)

    assert state0["source"] == state1["source"] == src_a.path()
    assert state0["output_index"] == 0
    assert state1["output_index"] == 1
    assert state0["hash"] != state1["hash"]


def test_phase5b_batch_connect_stale_preflight_performs_zero_writes():
    hou, src_a, src_b, target = make_hou()
    target.setInput(0, src_a, 0)
    target.setInput(1, src_a, 1)
    before0 = node_ops.input_state(hou, target.path(), 0)
    before1 = node_ops.input_state(hou, target.path(), 1)

    result = node_ops.batch_connect(
        hou,
        [
            {
                "target": target.path(),
                "input_index": 0,
                "source": src_b.path(),
                "output_index": 0,
                "expected_hash": before0["hash"],
            },
            {
                "target": target.path(),
                "input_index": 1,
                "source": src_b.path(),
                "output_index": 1,
                "expected_hash": "stale",
            },
        ],
    )

    assert result["conflict"] is True
    assert result["written"] == 0
    assert node_ops.input_state(hou, target.path(), 0)["hash"] == before0["hash"]
    assert node_ops.input_state(hou, target.path(), 1)["hash"] == before1["hash"]


def test_phase5b_batch_connect_success_verifies_source_and_output():
    hou, src_a, src_b, target = make_hou()
    before0 = node_ops.input_state(hou, target.path(), 0)
    before1 = node_ops.input_state(hou, target.path(), 1)

    result = node_ops.batch_connect(
        hou,
        [
            {
                "target": target.path(),
                "input_index": 0,
                "source": src_a.path(),
                "output_index": 2,
                "expected_hash": before0["hash"],
            },
            {
                "target": target.path(),
                "input_index": 1,
                "source": src_b.path(),
                "output_index": 1,
                "expected_hash": before1["hash"],
            },
        ],
    )

    assert result["conflict"] is False
    assert result["verified"] is True
    assert result["written"] == 2
    after0 = node_ops.input_state(hou, target.path(), 0)
    after1 = node_ops.input_state(hou, target.path(), 1)
    assert after0["source"] == src_a.path()
    assert after0["output_index"] == 2
    assert after1["source"] == src_b.path()
    assert after1["output_index"] == 1


def test_phase5b_batch_connect_rolls_back_prior_writes_on_exception():
    hou, src_a, src_b, target = make_hou()
    target.setInput(0, src_a, 0)
    target.setInput(1, src_a, 1)
    before0 = node_ops.input_state(hou, target.path(), 0)
    before1 = node_ops.input_state(hou, target.path(), 1)

    target.fail_once_on(1)
    result = node_ops.batch_connect(
        hou,
        [
            {
                "target": target.path(),
                "input_index": 0,
                "source": src_b.path(),
                "output_index": 0,
                "expected_hash": before0["hash"],
            },
            {
                "target": target.path(),
                "input_index": 1,
                "source": src_b.path(),
                "output_index": 1,
                "expected_hash": before1["hash"],
            },
        ],
    )

    assert result["verified"] is False
    assert result["rolled_back"] is True
    assert result["written"] == 0
    assert node_ops.input_state(hou, target.path(), 0)["hash"] == before0["hash"]
    assert node_ops.input_state(hou, target.path(), 1)["hash"] == before1["hash"]


def test_phase5b_batch_connect_requires_hashes_and_unique_target_inputs():
    hou, src_a, _, target = make_hou()
    before = node_ops.input_state(hou, target.path(), 0)

    with pytest.raises(ValueError, match="EXPECTED_HASH_REQUIRED"):
        node_ops.batch_connect(
            hou,
            [{
                "target": target.path(),
                "input_index": 0,
                "source": src_a.path(),
                "output_index": 0,
            }],
        )

    with pytest.raises(ValueError, match="ARGUMENT_INVALID"):
        node_ops.batch_connect(
            hou,
            [
                {
                    "target": target.path(),
                    "input_index": 0,
                    "source": src_a.path(),
                    "output_index": 0,
                    "expected_hash": before["hash"],
                },
                {
                    "target": target.path(),
                    "input_index": 0,
                    "source": src_a.path(),
                    "output_index": 1,
                    "expected_hash": before["hash"],
                },
            ],
        )


def test_phase5b_dispatcher_and_recipe_engine_expose_atomic_connection_ops():
    dispatcher = (ADAPTER_PY / "ai_bridge_houdini" / "dispatcher.py").read_text(encoding="utf-8")
    registry = (ADAPTER_PY / "ai_bridge_houdini" / "knowledge_registry.py").read_text(encoding="utf-8")
    client = (ADAPTER_PY / "ai_bridge_houdini" / "client.py").read_text(encoding="utf-8")

    assert 'op == "node.input_state"' in dispatcher
    assert 'op == "node.batch_connect"' in dispatcher
    assert '"node.input_state"' in registry
    assert '"node.batch_connect"' in registry
    assert '"node.input_state"' in client
    assert '"node.batch_connect"' in client
