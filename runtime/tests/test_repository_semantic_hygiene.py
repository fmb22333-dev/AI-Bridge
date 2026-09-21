from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"


def test_only_current_supervisor_payload_is_kept_in_product_tree():
    manifest = json.loads((ROOT / "supervisor-release.json").read_text(encoding="utf-8"))
    current = str(manifest["version"])
    versions = sorted(
        path.name
        for path in (ROOT / "bootstrap" / "supervisor").iterdir()
        if path.is_dir()
    )
    assert versions == [current]


def test_runtime_does_not_ship_parallel_historical_authority_docs_or_dead_primary_extension():
    assert not (RUNTIME / "src" / "ai_bridge" / "docs").exists()
    assert not (
        RUNTIME / "src" / "ai_bridge" / "transport" / "supabase_primary_extension.py"
    ).exists()


def test_web_ui_does_not_encode_runtime_version_as_cache_key():
    web = RUNTIME / "src" / "ai_bridge" / "web"
    for path in (
        web / "templates" / "index.html",
        web / "templates" / "commands.html",
        web / "fallback_routes.py",
    ):
        assert "?v=" not in path.read_text(encoding="utf-8")


def test_current_supabase_authority_is_not_duplicated_into_legacy_doc():
    legacy = (ROOT / "docs" / "SUPABASE_FALLBACK_TRANSPORT.md").read_text(encoding="utf-8")
    assert "Compatibility Note" in legacy
    assert "SUPABASE_PRIMARY_TRANSPORT.md" in legacy
    assert "## Table contract" not in legacy
    assert "## Security model" not in legacy


def test_unreal_docs_match_ready_plugin_status():
    host_plugins = json.loads(
        (RUNTIME / "src" / "ai_bridge" / "host_plugins.json").read_text(encoding="utf-8")
    )
    unreal = next(item for item in host_plugins["hosts"] if item["id"] == "unreal")
    readme = (RUNTIME / "unreal_adapter" / "README.md").read_text(encoding="utf-8")
    cpp_readme = (RUNTIME / "unreal_adapter" / "cpp" / "README.md").read_text(encoding="utf-8")
    assert unreal["state"] == "ready"
    assert "scaffold only" not in readme.lower()
    assert "No implementation is active yet" not in cpp_readme
    assert "AIBridgeUE" in readme
