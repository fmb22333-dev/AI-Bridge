from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
for path in (SRC, ADAPTER_PY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionRegistration
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin
from ai_bridge_houdini import client as houdini_client
from ai_bridge_houdini import knowledge_registry


PROJECT = "E:/Houdini/Test/project.hip"


class RecipeFailureExecutor:
    def __init__(self, checkpoint_path: str):
        self.checkpoint_path = checkpoint_path
        self.calls = []

    def execute(self, command):
        self.calls.append(command.operation)
        if command.operation == "checkpoint.create":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": self.checkpoint_path,
                    "original_hip": PROJECT,
                },
            )
        if command.operation == "recipe.apply":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.FAILED,
                result={
                    "verified": False,
                    "failure": {
                        "code": "RECIPE_STEP_FAILED",
                        "message": "Cook failed",
                    },
                },
                failure=FailureInfo(
                    origin=FailureOrigin.HOST,
                    code="RECIPE_STEP_FAILED",
                    message="Cook failed",
                ),
            )
        if command.operation == "rollback.execute":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": command.arguments["checkpoint_path"],
                    "original_hip": command.arguments["original_hip"],
                    "current_hip": command.arguments["original_hip"],
                },
            )
        raise AssertionError(command.operation)


class RecipeDeniedExecutor(RecipeFailureExecutor):
    def execute(self, command):
        self.calls.append(command.operation)
        if command.operation == "checkpoint.create":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": self.checkpoint_path,
                    "original_hip": PROJECT,
                },
            )
        if command.operation == "recipe.apply":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.DENIED,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER,
                    code="RECIPE_NOT_PROMOTED",
                ),
            )
        raise AssertionError(command.operation)


def _service(tmp_path, executor):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", workspace)
    service = BridgeService(
        db=BridgeDB(tmp_path / "bridge.db"),
        workspaces=workspaces,
    )
    service.register_adapter_session(SessionRegistration.model_validate({
        "session_id": "HOU-TEST",
        "adapter": "houdini",
        "adapter_version": "0.5.20",
        "host_version": "21.0.440",
        "pid": 1234,
        "project_file": PROJECT,
        "capabilities": [
            {
                "name": "checkpoint.create",
                "version": "1.0",
                "write": True,
                "risk": "L1",
                "rollback": False,
                "verification": True,
                "tested_host_versions": ["21.0"],
            },
            {
                "name": "rollback.execute",
                "version": "1.0",
                "write": True,
                "risk": "L2",
                "rollback": False,
                "verification": True,
                "tested_host_versions": ["21.0"],
            },
            {
                "name": "recipe.apply",
                "version": "1.0",
                "write": True,
                "risk": "L2",
                "rollback": True,
                "verification": True,
                "rollback_on_failure": True,
                "tested_host_versions": ["21.0"],
            },
        ],
    }))
    service.register_executor("houdini", executor)
    return service


def _command(command_id="recipe-failure"):
    return CommandEnvelope.model_validate({
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-TEST",
        "project_file": PROJECT,
        "operation": "recipe.apply",
        "arguments": {
            "recipe": "code.safe_patch_and_cook",
            "values": {
                "path": "/obj/geo1/wrangle1",
                "parameter": "snippet",
                "old_text": "old",
                "new_text": "new",
                "cook_path": "/obj/geo1/wrangle1",
            },
        },
        "execution": {
            "verify": True,
            "checkpoint": "auto",
            "dry_run": False,
            "auto_recover": False,
        },
        "risk": "L2",
    })


def test_recipe_apply_capability_explicitly_requests_failure_rollback():
    item = next(row for row in houdini_client.CAPABILITIES if row["name"] == "recipe.apply")
    assert item["rollback_on_failure"] is True


def test_core_recipe_failure_rolls_back_verified_checkpoint_and_preserves_failure(tmp_path):
    checkpoint = tmp_path / "project_bak1.hip"
    checkpoint.write_text("backup", encoding="utf-8")
    executor = RecipeFailureExecutor(str(checkpoint))
    service = _service(tmp_path, executor)

    result = service.execute(_command())

    assert result.status == ExecutionStatus.FAILED
    assert result.failure.code == "RECIPE_STEP_FAILED"
    assert executor.calls == ["checkpoint.create", "recipe.apply", "rollback.execute"]
    rollback = result.result["_bridge"]["failure_rollback"]
    assert rollback["status"] == "success"
    assert rollback["verified"] is True
    assert rollback["checkpoint_path"] == str(checkpoint)
    assert result.last_known_state["project_state"] == "rolled_back_to_checkpoint"
    assert result.last_known_state["rollback_checkpoint_id"].startswith("cp_recipe-failure_")


def test_denied_recipe_does_not_reload_checkpoint(tmp_path):
    checkpoint = tmp_path / "project_bak1.hip"
    checkpoint.write_text("backup", encoding="utf-8")
    executor = RecipeDeniedExecutor(str(checkpoint))
    service = _service(tmp_path, executor)

    result = service.execute(_command("recipe-denied"))

    assert result.status == ExecutionStatus.DENIED
    assert result.failure.code == "RECIPE_NOT_PROMOTED"
    assert executor.calls == ["checkpoint.create", "recipe.apply"]
    assert "failure_rollback" not in result.result.get("_bridge", {})


def test_code_safe_recipe_collects_host_errors_after_cook_failure(monkeypatch):
    recipe = knowledge_registry.get_recipe("code.safe_patch_and_cook")
    cook_step = next(step for step in recipe["steps"] if step["op"] == "cook.execute")
    assert cook_step["stop_on_failure"] is False

    calls = []

    def fake_primitive(hou, op, args):
        calls.append(op)
        if op == "code.read":
            return {"text": "old", "hash": "hash-old"}
        if op == "code.patch":
            assert args["expected_hash"] == "hash-old"
            return {"verified": True, "before": {"hash": "hash-old"}, "after": {"hash": "hash-new"}}
        if op == "cook.execute":
            return {"cook_status": "FAILED", "errors": ["compile error"]}
        if op == "host.errors":
            return {"errors": ["compile error"], "warnings": []}
        raise AssertionError(op)

    monkeypatch.setattr(knowledge_registry, "_execute_primitive", fake_primitive)
    result = knowledge_registry.apply_recipe(
        object(),
        "code.safe_patch_and_cook",
        {
            "path": "/obj/geo1/wrangle1",
            "parameter": "snippet",
            "old_text": "old",
            "new_text": "new",
            "cook_path": "/obj/geo1/wrangle1",
        },
    )

    assert calls == ["code.read", "code.patch", "cook.execute", "host.errors"]
    assert result["verified"] is False
    assert [row["status"] for row in result["trace"]] == ["PASS", "PASS", "FAILED", "PASS"]
    assert result["failure"]["code"] == "RECIPE_STEP_FAILED"
    assert result["failure"]["operation"] == "cook.execute"
    assert result["results"]["errors"]["errors"] == ["compile error"]


def test_code_safe_recipe_success_skips_error_collection(monkeypatch):
    calls = []

    def fake_primitive(hou, op, args):
        calls.append(op)
        if op == "code.read":
            return {"text": "old", "hash": "hash-old"}
        if op == "code.patch":
            return {"verified": True}
        if op == "cook.execute":
            return {"cook_status": "PASS", "errors": []}
        if op == "host.errors":
            raise AssertionError("host.errors must be conditionally skipped after PASS")
        raise AssertionError(op)

    monkeypatch.setattr(knowledge_registry, "_execute_primitive", fake_primitive)
    result = knowledge_registry.apply_recipe(
        object(),
        "code.safe_patch_and_cook",
        {
            "path": "/obj/geo1/wrangle1",
            "parameter": "snippet",
            "old_text": "old",
            "new_text": "new",
            "cook_path": "/obj/geo1/wrangle1",
        },
    )

    assert calls == ["code.read", "code.patch", "cook.execute"]
    assert result["verified"] is True
    assert result["failure"] is None
    assert result["trace"][-1]["op"] == "host.errors"
    assert result["trace"][-1]["status"] == "SKIPPED"


class RecipeValidationExecutor(RecipeFailureExecutor):
    def execute(self, command):
        self.calls.append(command.operation)
        if command.operation == "checkpoint.create":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": self.checkpoint_path,
                    "original_hip": PROJECT,
                },
            )
        if command.operation == "recipe.apply_validation":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={"verified": True, "validation_only": True},
            )
        if command.operation == "rollback.execute":
            return ExecutionResult(
                command_id=command.command_id,
                status=ExecutionStatus.SUCCESS,
                result={
                    "verified": True,
                    "checkpoint_path": command.arguments["checkpoint_path"],
                    "original_hip": command.arguments["original_hip"],
                    "current_hip": command.arguments["original_hip"],
                },
            )
        raise AssertionError(command.operation)


def test_recipe_apply_validation_capability_always_rolls_back():
    item = next(row for row in houdini_client.CAPABILITIES if row["name"] == "recipe.apply_validation")
    assert item["rollback_after_execution"] is True
    assert item["risk"] == "L2"
    assert item["write"] is True


def test_core_validation_success_is_rolled_back(tmp_path):
    checkpoint = tmp_path / "project_bak_validation.hip"
    checkpoint.write_text("backup", encoding="utf-8")
    executor = RecipeValidationExecutor(str(checkpoint))
    service = _service(tmp_path, executor)
    registration = service.sessions.get("HOU-TEST")
    caps = list(registration.capabilities)
    from ai_bridge.protocol.capability import CapabilityDescriptor
    caps.append(CapabilityDescriptor.model_validate({
        "name": "recipe.apply_validation",
        "version": "1.0",
        "write": True,
        "risk": "L2",
        "rollback": True,
        "verification": True,
        "rollback_after_execution": True,
        "tested_host_versions": ["21.0"],
    }))
    from dataclasses import replace
    service.sessions._items["HOU-TEST"] = replace(registration, capabilities=tuple(caps))

    command = _command("recipe-validation-success").model_copy(update={
        "operation": "recipe.apply_validation",
    })
    result = service.execute(command)

    assert result.status == ExecutionStatus.SUCCESS
    assert executor.calls == ["checkpoint.create", "recipe.apply_validation", "rollback.execute"]
    assert result.result["_bridge"]["validation_original_status"] == "success"
    assert result.result["_bridge"]["validation_rollback"]["verified"] is True
    assert result.last_known_state["validation_only"] is True
    assert result.last_known_state["project_state"] == "rolled_back_to_checkpoint"


def test_code_safe_patch_and_cook_is_promoted_after_live_transaction_validation():
    assert knowledge_registry.recipe_promotion_state("code.safe_patch_and_cook") == "promoted"
    entry = next(
        item for item in knowledge_registry.promotion_entries()
        if item["id"] == "recipe.code.safe_patch_and_cook"
    )
    assert entry["state"] == "promoted"
    assert entry["validation"]["regression_pass"] is True
    assert entry["validation"]["live_or_baseline_pass"] is True
    assert "21.0.440" in entry["supported_host_versions"]
