from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters.scene_store import SceneStoreExecutor, descriptor as scene_store_descriptor
from ai_bridge.persistence.scene_captures import SceneCaptureStore
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.transport.local_api import create_app


def _sample_scene():
    shared = "/Game/Fluid/T_Surface_A.T_Surface_A"
    return {
        "schema_version": "aibridge_ue_scene/1",
        "read_only": True,
        "engine_version": "5.6.1",
        "project_name": "Demo01",
        "plugin_version": "0.4.0",
        "world_name": "Test",
        "world_path": "/Game/Maps/Test.Test",
        "world_package": "/Game/Maps/Test",
        "actors": [
            {
                "name": "FluxA",
                "path": "/Game/Maps/Test.Test:PersistentLevel.FluxA",
                "class": "BP_FluxSurface_C",
                "class_path": "/Game/BP_FluxSurface.BP_FluxSurface_C",
                "label": "Flux A",
                "location": {"x": 1, "y": 2, "z": 3},
                "rotation": {"pitch": 0, "yaw": 0, "roll": 0},
                "scale": {"x": 1, "y": 1, "z": 1},
                "bounds_origin": {"x": 1, "y": 2, "z": 3},
                "bounds_extent": {"x": 100, "y": 100, "z": 10},
                "properties": [
                    {"name": "Resolution", "type": "IntProperty", "value": "2048"}
                ],
                "asset_references": [
                    {
                        "property": "Surface",
                        "object_path": shared,
                        "class": "Texture2D",
                        "class_path": "/Script/Engine.Texture2D",
                        "is_asset": True,
                        "soft": False,
                    }
                ],
                "components": [
                    {
                        "name": "FluxComp",
                        "path": "/Game/Maps/Test.Test:PersistentLevel.FluxA.FluxComp",
                        "class": "FluxComponent",
                        "class_path": "/Script/FluidFlux.FluxComponent",
                        "properties": [
                            {"name": "Enabled", "type": "BoolProperty", "value": "True"}
                        ],
                        "asset_references": [
                            {
                                "property": "Surface",
                                "object_path": shared,
                                "class": "Texture2D",
                                "class_path": "/Script/Engine.Texture2D",
                                "is_asset": True,
                                "soft": False,
                            }
                        ],
                    }
                ],
                "component_count": 1,
            },
            {
                "name": "FluxB",
                "path": "/Game/Maps/Test.Test:PersistentLevel.FluxB",
                "class": "BP_FluxSurface_C",
                "class_path": "/Game/BP_FluxSurface.BP_FluxSurface_C",
                "properties": [],
                "asset_references": [
                    {
                        "property": "Surface",
                        "object_path": shared,
                        "class": "Texture2D",
                        "class_path": "/Script/Engine.Texture2D",
                        "is_asset": True,
                        "soft": False,
                    }
                ],
                "components": [],
                "component_count": 0,
            },
        ],
        "loaded_actor_count": 2,
        "matching_actor_count": 2,
        "actors_truncated": False,
    }


def test_capture_keeps_full_raw_scene_but_returns_compact_summary(tmp_path):
    store = SceneCaptureStore(tmp_path)
    scene = _sample_scene()
    summary = store.capture(command_id="cmd:1", session_id="UE-1", scene=scene)
    assert "actors" not in summary
    assert summary["capture_id"].startswith("cap_")
    assert summary["counts"] == {
        "actors": 2, "components": 1, "properties": 2, "references": 3,
        "unique_assets": 1, "unique_classes": 2,
    }
    assert store.read(summary["capture_id"])["scene"] == scene


def test_query_filters_and_projects_snapshot_without_recontacting_ue(tmp_path):
    store = SceneCaptureStore(tmp_path)
    capture = store.capture(command_id="cmd:1", session_id="UE-1", scene=_sample_scene())
    result = store.query({
        "capture_id": capture["capture_id"], "class_contains": "FluxSurface",
        "text": "FluxA", "include": ["identity", "references"], "max_items": 8,
    })
    assert result["matched_actor_count"] == 1
    assert result["returned_count"] == 1
    actor = result["actors"][0]
    assert actor["name"] == "FluxA"
    assert "asset_references" in actor
    assert "properties" not in actor
    assert "components" not in actor


def test_query_is_bounded(tmp_path):
    store = SceneCaptureStore(tmp_path)
    capture = store.capture(command_id="cmd:1", session_id="UE-1", scene=_sample_scene())
    result = store.query({"capture_id": capture["capture_id"], "max_items": 1})
    assert result["matched_actor_count"] == 2
    assert result["returned_count"] == 1
    assert result["truncated"] is True


class FakeSessions:
    def __init__(self, adapter="unreal"):
        self.adapter = adapter
    def touch(self, session_id):
        if session_id != "UE-TEST":
            raise KeyError(session_id)
        return SimpleNamespace(adapter=self.adapter)
    def list(self):
        return []


class FakeBus:
    def __init__(self):
        self.completed = []
    def complete(self, result):
        self.completed.append(result)
        return True
    def poll(self, session_id, timeout=0.0):
        return None


class FakeStop:
    write_blocked = False


class FakeService:
    def __init__(self, store, adapter="unreal"):
        self.sessions = FakeSessions(adapter=adapter)
        self.adapter_bus = FakeBus()
        self.emergency_stop = FakeStop()
        self.scene_captures = store


def _headers():
    return {"Authorization": "Bearer test-token"}


def test_unreal_result_ingress_persists_raw_and_completes_with_summary(tmp_path):
    store = SceneCaptureStore(tmp_path)
    service = FakeService(store)
    client = TestClient(create_app(service, auth_token="test-token"))
    response = client.post("/adapter/result/UE-TEST", headers=_headers(), json={
        "command_id": "cmd-scene", "status": "success",
        "stages": {"READBACK": "VERIFIED"}, "result": _sample_scene(),
        "failure": None, "rollback_available": False,
        "last_known_state": {"host": "alive"}, "evidence_id": None,
    })
    assert response.status_code == 200
    terminal = service.adapter_bus.completed[-1]
    assert terminal.result["capture_id"].startswith("cap_")
    assert "actors" not in terminal.result
    raw = store.read(terminal.result["capture_id"])
    assert len(raw["scene"]["actors"]) == 2


def test_non_unreal_result_ingress_is_not_rewritten(tmp_path):
    store = SceneCaptureStore(tmp_path)
    service = FakeService(store, adapter="houdini")
    client = TestClient(create_app(service, auth_token="test-token"))
    payload = {"value": 7, "actors": [1, 2, 3]}
    response = client.post("/adapter/result/UE-TEST", headers=_headers(), json={
        "command_id": "cmd-hou", "status": "success", "result": payload,
    })
    assert response.status_code == 200
    assert service.adapter_bus.completed[-1].result == payload


def test_scene_store_adapter_exposes_query_capability_and_executes_it(tmp_path):
    store = SceneCaptureStore(tmp_path)
    capture = store.capture(command_id="cmd:1", session_id="UE-1", scene=_sample_scene())
    executor = SceneStoreExecutor(store=store)
    command = CommandEnvelope(
        command_id="cmd-query", workspace="Bridge", adapter="scene_store",
        operation="scene.query", arguments={"capture_id": capture["capture_id"], "max_items": 1},
    )
    result = executor.execute(command)
    assert result.status.value == "success"
    assert result.result["returned_count"] == 1
    assert any(cap.name == "scene.query" for cap in scene_store_descriptor().capabilities)
