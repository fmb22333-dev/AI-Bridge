from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import capability_guidance, compat_ops, dispatcher, knowledge_registry


class FakeHipFile:
    def path(self):
        return "E:/Generic/Project.hip"


class FakeHou:
    hipFile = FakeHipFile()

    def applicationVersionString(self):
        return "21.0.440"


def setup_function():
    capability_guidance._reset_runtime_state_for_tests()


def test_guidance_catalog_is_generic_and_candidate_is_not_promoted():
    catalog = capability_guidance.catalog(promoted_only=False)
    text = json.dumps(catalog, ensure_ascii=False).lower()

    for forbidden in ("autouv", "paired sheet", "beveled disk", "skirt"):
        assert forbidden not in text

    by_capability = {item["capability"]: item for item in catalog["entries"]}
    assert by_capability["diagnostic.transaction"]["state"] == "candidate"

    promoted = {
        item["capability"]
        for item in capability_guidance.catalog(promoted_only=True)["entries"]
    }
    assert "diagnostic.transaction" not in promoted
    assert "network.ensure_transactional" in promoted


def test_repeated_low_level_reads_return_non_blocking_batch_suggestion():
    assert capability_guidance.observe("S1", "parm.read", now=1.0) == []
    suggestions = capability_guidance.observe("S1", "parm.read", now=2.0)

    assert len(suggestions) == 1
    item = suggestions[0]
    assert item["capability"] == "parm.batch_read"
    assert item["advisory_only"] is True
    assert item["blocking"] is False
    assert item["auto_execute"] is False

    # Cooldown prevents suggestion spam for the same rule.
    assert capability_guidance.observe("S1", "parm.read", now=3.0) == []


def test_generic_network_sequence_suggests_transaction_without_blocking():
    assert capability_guidance.observe("S2", "node.create_configured", now=10.0) == []
    assert capability_guidance.observe("S2", "node.connect", now=11.0) == []
    suggestions = capability_guidance.observe("S2", "cook.execute", now=12.0)

    assert [item["capability"] for item in suggestions] == ["network.ensure_transactional"]
    assert suggestions[0]["blocking"] is False


def test_adapter_capabilities_exposes_promoted_guidance_and_candidate_catalog():
    hou = FakeHou()
    session = {
        "session_id": "HOU-GENERIC",
        "adapter_version": "0.5.13",
        "capabilities": [
            {"name": "parm.batch_read", "write": False},
            {"name": "inspect.node", "write": False},
        ],
    }
    payload = compat_ops.adapter_capabilities(hou, session)

    by_name = {item["name"]: item for item in payload["capabilities"]}
    assert by_name["parm.batch_read"]["guidance"]["kind"] == "generic_sugar"
    assert "guidance" not in by_name["inspect.node"]

    all_guidance = {
        item["capability"]: item
        for item in payload["capability_guidance"]["entries"]
    }
    assert all_guidance["diagnostic.transaction"]["state"] == "candidate"
    assert payload["capability_guidance"]["policy"]["blocking"] is False


def test_dispatch_success_can_attach_advisory_guidance_without_changing_status():
    dispatcher._dispatch_context.operation = "parm.read"
    first = dispatcher._result("c1", "S3", {"value": 1}, stages={"READBACK": "VERIFIED"})
    assert first["status"] == "success"
    assert "guidance" not in first

    dispatcher._dispatch_context.operation = "parm.read"
    second = dispatcher._result("c2", "S3", {"value": 2}, stages={"READBACK": "VERIFIED"})
    assert second["status"] == "success"
    assert second["stages"] == {"READBACK": "VERIFIED"}
    assert second["guidance"]["mode"] == "advisory_only"
    assert second["guidance"]["blocking"] is False
    assert second["guidance"]["suggestions"][0]["capability"] == "parm.batch_read"


def test_knowledge_status_and_search_include_capability_guidance():
    status = knowledge_registry.status()
    assert status["capability_guidance_count"] >= 5
    assert status["capability_guidance_promoted_count"] >= 4
    assert status["capability_guidance_candidate_count"] >= 1
    assert status["capability_guidance"]["blocking"] is False

    result = knowledge_registry.search("diagnostic transaction")
    hits = [
        item for item in result["results"]
        if item.get("kind") == "capability_guidance"
    ]
    assert hits
    assert hits[0]["state"] == "candidate"


def test_promotion_lifecycle_keeps_router_validated_and_transaction_candidate():
    entries = {item["id"]: item for item in knowledge_registry.promotion_entries()}

    router = entries["mechanism.capability_guidance.advisory_router"]
    assert router["state"] == "validated"
    assert router["validation"]["regression_pass"] is True
    assert router["validation"]["live_or_baseline_pass"] is False

    transaction = entries["orchestration.diagnostic.transaction"]
    assert transaction["state"] == "candidate"
    assert transaction["validation"]["regression_pass"] is False
    assert transaction["bounded_contract"]["low_level_fallback_required"] is True
