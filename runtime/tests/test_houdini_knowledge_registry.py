from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ADAPTER_PY=ROOT/"houdini_adapter"/"python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0,str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")


def test_seed_knowledge_is_present():
    ids={item["id"] for item in knowledge_registry.recipes()}
    assert {"network.build","network.build_and_cook","code.safe_patch_and_cook"} <= ids
    state=knowledge_registry.status()
    assert state["hot_reload"] is True
    assert state["restart_required_for_knowledge_changes"] is False
    assert state["recipe_count"] >= 3
    assert state["error_rule_count"] >= 4
    assert state["host_rule_count"] >= 9


def test_recipe_files_hot_reload_without_module_reload(tmp_path, monkeypatch):
    recipes_dir=tmp_path/"recipes"
    error_file=tmp_path/"error_catalog.json"
    rules_file=tmp_path/"host_rules.json"
    _write_json(error_file,{"rules":[]})
    _write_json(rules_file,{"rules":[]})
    monkeypatch.setattr(knowledge_registry,"RECIPES_DIR",recipes_dir)
    monkeypatch.setattr(knowledge_registry,"ERROR_CATALOG",error_file)
    monkeypatch.setattr(knowledge_registry,"HOST_RULES",rules_file)

    _write_json(recipes_dir/"a.json",{
        "id":"demo.a","version":"1.0","description":"first","required":[],"steps":[]
    })
    assert [item["id"] for item in knowledge_registry.recipes()] == ["demo.a"]
    digest_a=knowledge_registry.status()["content_digest"]

    _write_json(recipes_dir/"b.json",{
        "id":"demo.b","version":"1.0","description":"second","required":[],"steps":[]
    })
    ids={item["id"] for item in knowledge_registry.recipes()}
    digest_b=knowledge_registry.status()["content_digest"]
    assert ids == {"demo.a","demo.b"}
    assert digest_b != digest_a


def test_error_catalog_hot_reload_without_module_reload(tmp_path, monkeypatch):
    recipes_dir=tmp_path/"recipes"
    error_file=tmp_path/"error_catalog.json"
    rules_file=tmp_path/"host_rules.json"
    recipes_dir.mkdir()
    _write_json(rules_file,{"rules":[]})
    _write_json(error_file,{"rules":[]})
    monkeypatch.setattr(knowledge_registry,"RECIPES_DIR",recipes_dir)
    monkeypatch.setattr(knowledge_registry,"ERROR_CATALOG",error_file)
    monkeypatch.setattr(knowledge_registry,"HOST_RULES",rules_file)

    assert knowledge_registry.match_error_text("ambiguous call") is None
    _write_json(error_file,{"rules":[{
        "id":"demo.error",
        "contains_any":["ambiguous call"],
        "code":"DEMO_AMBIGUOUS",
        "category":"demo",
        "suggestion":"typed temporary"
    }]})
    match=knowledge_registry.match_error_text("VEX ambiguous call to foo")
    assert match is not None
    assert match["code"]=="DEMO_AMBIGUOUS"


def test_recipe_resolution_uses_arguments_and_previous_results(tmp_path, monkeypatch):
    recipes_dir=tmp_path/"recipes"
    error_file=tmp_path/"error_catalog.json"
    rules_file=tmp_path/"host_rules.json"
    _write_json(error_file,{"rules":[]})
    _write_json(rules_file,{"rules":[]})
    monkeypatch.setattr(knowledge_registry,"RECIPES_DIR",recipes_dir)
    monkeypatch.setattr(knowledge_registry,"ERROR_CATALOG",error_file)
    monkeypatch.setattr(knowledge_registry,"HOST_RULES",rules_file)

    _write_json(recipes_dir/"patch.json",{
        "id":"demo.patch",
        "version":"1.0",
        "required":["path"],
        "steps":[
            {"op":"code.read","save_as":"read","arguments":{"path":{"$arg":"path"},"parameter":"snippet"}},
            {"op":"code.patch","save_as":"patch","arguments":{
                "path":{"$arg":"path"},
                "parameter":"snippet",
                "old_text":"a",
                "new_text":"b",
                "expected_hash":{"$result":"read.hash"}
            }}
        ]
    })

    calls=[]
    def fake_execute(hou,op,args):
        calls.append((op,args))
        if op=="code.read":
            return {"hash":"abc123"}
        return {"verified":True}

    monkeypatch.setattr(knowledge_registry,"_execute_primitive",fake_execute)
    out=knowledge_registry.apply_recipe(object(),"demo.patch",{"path":"/obj/wrangle"})
    assert out["verified"] is True
    assert calls[1][1]["expected_hash"]=="abc123"


def test_knowledge_search_finds_host_rules_and_recipes():
    recipe=knowledge_registry.search("network",limit=50)
    assert any(item["kind"]=="recipe" for item in recipe["results"])
    host=knowledge_registry.search("checkpoint",limit=50)
    assert any(item["kind"]=="host_rule" for item in host["results"])


def test_dispatcher_has_stable_knowledge_entrypoints():
    text=(ADAPTER_PY/"ai_bridge_houdini"/"dispatcher.py").read_text(encoding="utf-8")
    for op in ("knowledge.status","knowledge.search","recipe.list","recipe.get","recipe.validate","recipe.apply"):
        assert f'op == "{op}"' in text
