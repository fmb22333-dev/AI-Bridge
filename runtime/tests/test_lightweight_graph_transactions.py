import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOUDINI_ADAPTER_PYTHON = ROOT / "houdini_adapter" / "python"
if str(HOUDINI_ADAPTER_PYTHON) not in sys.path:
    sys.path.insert(0, str(HOUDINI_ADAPTER_PYTHON))

from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.capability import CapabilityDescriptor
from ai_bridge.protocol.command import CommandEnvelope, RiskLevel
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge_houdini import client as houdini_client
from ai_bridge_houdini import node_ops


class _Type:
    def __init__(self, name):
        self._name = name
    def name(self):
        return self._name


class _Node:
    def __init__(self, hou, path, node_type="null", code="node-v1"):
        self.hou = hou
        self._path = path
        self._name = path.rsplit("/", 1)[-1]
        self._type = _Type(node_type)
        self.code = code
        self._inputs = {}
    def path(self): return self._path
    def name(self): return self._name
    def type(self): return self._type
    def asCode(self, recurse=True): return self.code
    def input(self, index): return self._inputs.get(int(index))
    def inputConnections(self): return []
    def setInput(self, index, source, output_index=0): self._inputs[int(index)] = source
    def destroy(self): self.hou.nodes.pop(self._path, None)


class _Parent(_Node):
    def node(self, name): return self.hou.nodes.get(self._path.rstrip("/") + "/" + name)
    def createNode(self, node_type, name, **kwargs):
        path = self._path.rstrip("/") + "/" + name
        node = _Node(self.hou, path, node_type=node_type)
        self.hou.nodes[path] = node
        return node


class _Hou:
    def __init__(self):
        self.nodes = {}
        parent = _Parent(self, "/obj/geo1", node_type="geo")
        self.nodes[parent.path()] = parent
    def node(self, path): return self.nodes.get(path)


def _capability(name):
    return next(item for item in houdini_client.CAPABILITIES if item["name"] == name)


def test_fast_graph_capabilities_own_local_checkpoint_but_delete_keeps_full_checkpoint():
    for name in ("node.create", "node.connect", "node.disconnect"):
        assert _capability(name).get("manages_checkpoint") is True
    assert not _capability("node.delete").get("manages_checkpoint", False)


def test_node_create_returns_lightweight_snapshot_and_delete_rejects_stale_state():
    hou = _Hou()
    result = node_ops.create(hou, "/obj/geo1", "null", "perf_tmp")
    assert result["verified"] is True
    assert result["before"]["exists"] is False
    assert result["after"]["path"] == "/obj/geo1/perf_tmp"
    assert result["after"]["hash"]

    node = hou.node("/obj/geo1/perf_tmp")
    node.code = "node-was-modified"
    stale = node_ops.delete(
        hou, "/obj/geo1/perf_tmp", "null", "perf_tmp",
        expected_hash=result["after"]["hash"],
    )
    assert stale["conflict"] is True
    assert hou.node("/obj/geo1/perf_tmp") is node


def test_connect_rolls_back_if_readback_verification_fails():
    hou = _Hou()
    source = _Node(hou, "/obj/geo1/src")
    target = _Node(hou, "/obj/geo1/dst")
    hou.nodes[source.path()] = source
    hou.nodes[target.path()] = target
    before = node_ops.input_state(hou, target.path(), 0)

    original_input_state = node_ops.input_state
    calls = {"count": 0}
    def mismatching_state(hou_arg, target_path, input_index):
        calls["count"] += 1
        state = original_input_state(hou_arg, target_path, input_index)
        if calls["count"] == 3:
            state = dict(state)
            state["source"] = "/wrong"
        return state
    node_ops.input_state = mismatching_state
    try:
        result = node_ops.connect(hou, target.path(), 0, source.path(), 0, before["hash"])
    finally:
        node_ops.input_state = original_input_state
    assert result["verified"] is False
    assert result["rolled_back"] is True
    assert target.input(0) is None


class _Executor:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.calls = []
    def execute(self, command):
        self.calls.append(command)
        if command.operation == "checkpoint.create":
            checkpoint = self.tmp_path / "cp.hip"
            checkpoint.write_text("checkpoint", encoding="utf-8")
            return ExecutionResult(
                command_id=command.command_id, status=ExecutionStatus.SUCCESS,
                result={"verified": True, "checkpoint_path": str(checkpoint), "original_hip": "E:/test.hip"},
            )
        if command.operation == "node.create":
            return ExecutionResult(
                command_id=command.command_id, status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "before": {"exists": False, "path": "/obj/geo1/perf_tmp"},
                    "after": {"exists": True, "path": "/obj/geo1/perf_tmp", "type": "null", "name": "perf_tmp", "hash": "after-hash"},
                },
            )
        if command.operation in {"node.delete", "rollback.execute"}:
            return ExecutionResult(command_id=command.command_id, status=ExecutionStatus.SUCCESS, result={"verified": True})
        raise AssertionError(command.operation)


def _service(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")
    workspaces = WorkspaceRegistry()
    workspaces.register("Houdini", tmp_path)
    service = BridgeService(db=db, workspaces=workspaces)
    caps = [
        CapabilityDescriptor(name="node.create", version="1.0", write=True, risk=RiskLevel.L2, rollback=True, manages_checkpoint=True),
        CapabilityDescriptor(name="node.delete", version="1.0", write=True, risk=RiskLevel.L2, rollback=True),
        CapabilityDescriptor(name="checkpoint.create", version="1.0", write=True, risk=RiskLevel.L1),
        CapabilityDescriptor(name="rollback.execute", version="1.0", write=True, risk=RiskLevel.L2),
    ]
    service.register_adapter_session(SessionRegistration(
        session_id="HOU-TEST", adapter="houdini", adapter_version="0.5.27",
        host_version="21.0.440", pid=1234, project_file="E:/test.hip", capabilities=caps,
    ))
    executor = _Executor(tmp_path)
    service.register_executor("houdini", executor)
    return service, executor


def test_node_create_skips_full_hip_checkpoint_and_keeps_manual_rollback(tmp_path):
    service, executor = _service(tmp_path)
    command = CommandEnvelope(
        command_id="cmd-create", workspace="Houdini", adapter="houdini",
        session="HOU-TEST", project_file="E:/test.hip", operation="node.create",
        arguments={"parent": "/obj/geo1", "node_type": "null", "name": "perf_tmp"},
        risk=RiskLevel.L2,
    )
    result = service.execute(command)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.rollback_available is True
    assert [item.operation for item in executor.calls] == ["node.create"]

    rollback = service.rollback_command("cmd-create")
    assert rollback.status == ExecutionStatus.SUCCESS
    delete = next(item for item in executor.calls if item.operation == "node.delete")
    assert delete.arguments["path"] == "/obj/geo1/perf_tmp"
    assert delete.arguments["expected_hash"] == "after-hash"


def test_node_delete_still_requires_full_checkpoint(tmp_path):
    service, executor = _service(tmp_path)
    command = CommandEnvelope(
        command_id="cmd-delete", workspace="Houdini", adapter="houdini",
        session="HOU-TEST", project_file="E:/test.hip", operation="node.delete",
        arguments={"path": "/obj/geo1/perf_tmp", "expected_type": "null", "expected_name": "perf_tmp"},
        risk=RiskLevel.L2,
    )
    result = service.execute(command)
    assert result.status == ExecutionStatus.SUCCESS
    assert [item.operation for item in executor.calls[:2]] == ["checkpoint.create", "node.delete"]
