from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PY = ROOT / "houdini_adapter" / "python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PY))

from ai_bridge_houdini import compat_ops, node_ops


def test_phase5b_batch_connect_accepts_target_plus_connections_alias():
    items, mode = node_ops.normalize_batch_connect_args(
        {
            "target": "/obj/geo1/inputFix",
            "connections": [
                {
                    "input_index": 0,
                    "source": "/obj/geo1/IMPORT_ANIM",
                    "output_index": 0,
                    "expected_hash": "h0",
                },
                {
                    "input_index": 2,
                    "source": "/obj/geo1/FRAMEINFO_SOURCE",
                    "output_index": 0,
                    "expected_hash": "h2",
                },
            ],
        }
    )
    assert mode == "target+connections"
    assert items[0]["target"] == "/obj/geo1/inputFix"
    assert items[1]["target"] == "/obj/geo1/inputFix"


def test_phase5b_capabilities_self_describe_atomic_connection_schema():
    schema = compat_ops.ARGUMENT_SCHEMAS["node.batch_connect"]
    assert "items" in schema["canonical"]
    assert schema["aliases"]["target+connections"]["target"] == "str"
    assert schema["preflight"].startswith("all expected input hashes")
    assert "restored" in schema["rollback"]
