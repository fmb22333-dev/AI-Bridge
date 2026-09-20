from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import compat_ops, knowledge_registry, parm_ops


def test_phase4_batch_read_accepts_path_plus_parameters_alias():
    items, mode = compat_ops.normalize_parm_batch_read_args(
        {"path": "/obj/geo1/IMPORT_ANIM", "parameters": ["fbxfile", "animfbxfile"]}
    )
    assert mode == "path+parameters"
    assert items == [
        {"path": "/obj/geo1/IMPORT_ANIM", "parameter": "fbxfile"},
        {"path": "/obj/geo1/IMPORT_ANIM", "parameter": "animfbxfile"},
    ]


def test_phase4_batch_read_rejects_missing_or_empty_inputs():
    with pytest.raises(ValueError, match="ARGUMENT_REQUIRED"):
        compat_ops.normalize_parm_batch_read_args({})
    with pytest.raises(ValueError, match="ARGUMENT_INVALID"):
        compat_ops.normalize_parm_batch_read_args(
            {"path": "/obj/geo1/IMPORT_ANIM", "parameters": []}
        )


def test_phase4_batch_write_accepts_common_path_plus_writes():
    items, mode = compat_ops.normalize_parm_batch_write_args(
        {
            "path": "/obj/geo1/START",
            "writes": [
                {"parameter": "a", "value": 1, "expected_hash": "ha"},
                {"parameter": "b", "value": 2, "expected_hash": "hb"},
            ],
        }
    )
    assert mode == "path+writes"
    assert items == [
        {"path": "/obj/geo1/START", "parameter": "a", "value": 1, "expected_hash": "ha"},
        {"path": "/obj/geo1/START", "parameter": "b", "value": 2, "expected_hash": "hb"},
    ]


def test_phase4_batch_write_preflight_conflict_causes_zero_mutation(monkeypatch):
    writes = []

    def fake_read(hou, path, parameter):
        return {
            "path": path,
            "parameter": parameter,
            "value": 0,
            "hash": {"a": "ha", "b": "hb"}[parameter],
        }

    def fake_write(hou, path, parameter, value, expected_hash):
        writes.append((path, parameter, value, expected_hash))
        return {"conflict": False, "verified": True}

    monkeypatch.setattr(parm_ops, "read", fake_read)
    monkeypatch.setattr(parm_ops, "write", fake_write)

    result = compat_ops.parm_batch_write(
        object(),
        [
            {"path": "/obj/x", "parameter": "a", "value": 1, "expected_hash": "ha"},
            {"path": "/obj/x", "parameter": "b", "value": 2, "expected_hash": "stale"},
        ],
    )

    assert result["conflict"] is True
    assert result["written"] == 0
    assert writes == []
    assert result["conflicts"][0]["parameter"] == "b"


def test_phase4_batch_write_requires_all_expected_hashes_before_mutation(monkeypatch):
    writes = []

    def fake_read(hou, path, parameter):
        return {"path": path, "parameter": parameter, "value": 0, "hash": "h"}

    def fake_write(hou, path, parameter, value, expected_hash):
        writes.append(parameter)
        return {"conflict": False, "verified": True}

    monkeypatch.setattr(parm_ops, "read", fake_read)
    monkeypatch.setattr(parm_ops, "write", fake_write)

    with pytest.raises(ValueError, match="EXPECTED_HASH_REQUIRED"):
        compat_ops.parm_batch_write(
            object(),
            [
                {"path": "/obj/x", "parameter": "a", "value": 1, "expected_hash": "h"},
                {"path": "/obj/x", "parameter": "b", "value": 2},
            ],
        )
    assert writes == []


def test_phase4_capabilities_self_describe_batch_argument_shapes():
    class Hip:
        @staticmethod
        def path():
            return "E:/AA/Test.hip"

    class Hou:
        hipFile = Hip()

        @staticmethod
        def applicationVersionString():
            return "21.0.440"

    caps = compat_ops.adapter_capabilities(
        Hou(),
        {
            "session_id": "HOU-TEST",
            "adapter_version": "0.5.4",
            "capabilities": [],
        },
    )
    schemas = caps["argument_schemas"]
    assert schemas["parm.batch_read"]["aliases"]["path+parameters"]["parameters"] == "list[str]"
    assert schemas["parm.batch_write"]["aliases"]["path+writes"]["writes"] == "list[write]"
    assert schemas["knowledge.search"]["matching"] == ["exact_substring", "all_tokens"]


def test_phase4_knowledge_search_matches_normalized_all_tokens():
    result = knowledge_registry.search("directional projection", limit=20)
    ids = {item["id"] for item in result["results"]}
    assert "root_motion.directional_projection" in ids
    hit = next(item for item in result["results"] if item["id"] == "root_motion.directional_projection")
    assert hit["_search"]["mode"] == "all_tokens"


def test_phase4_knowledge_search_handles_spacing_against_alias_tokens():
    result = knowledge_registry.search("fbx animation file", limit=20)
    ids = {item["id"] for item in result["results"]}
    assert "fbx_character_import.file_parameters" in ids


def test_phase4_knowledge_search_ranks_exact_substring_before_token_only():
    result = knowledge_registry.search("animfbxfile", limit=20)
    assert result["results"]
    assert result["results"][0]["_search"]["mode"] == "exact_substring"
    scores = [item["_search"]["score"] for item in result["results"]]
    assert scores == sorted(scores, reverse=True)
