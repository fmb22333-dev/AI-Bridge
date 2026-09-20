import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ADAPTER_PY=ROOT/'houdini_adapter'/'python'
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0,str(ADAPTER_PY))
from ai_bridge_houdini import knowledge_registry


def _write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload),encoding='utf-8')


def test_invalid_recipe_file_keeps_last_known_good(tmp_path,monkeypatch):
    recipes=tmp_path/'recipes'; errors=tmp_path/'error_catalog.json'; rules=tmp_path/'host_rules.json'
    _write(recipes/'good.json',{'id':'demo.good','version':'1','required':[],'steps':[]})
    _write(errors,{'rules':[]}); _write(rules,{'rules':[]})
    monkeypatch.setattr(knowledge_registry,'RECIPES_DIR',recipes)
    monkeypatch.setattr(knowledge_registry,'ERROR_CATALOG',errors)
    monkeypatch.setattr(knowledge_registry,'HOST_RULES',rules)
    assert [r['id'] for r in knowledge_registry.recipes()]==['demo.good']
    (recipes/'good.json').write_text('{broken json',encoding='utf-8')
    assert [r['id'] for r in knowledge_registry.recipes()]==['demo.good']
    state=knowledge_registry.status()
    assert state.get('knowledge_degraded') is True
    assert state.get('validation_errors')


def test_repair_guard_detects_same_rule_same_state_loop():
    guard=knowledge_registry.RepairLoopGuard(max_attempts=3)
    first=guard.check('rule.a','state-1')
    second=guard.check('rule.a','state-1')
    assert first['ok'] is True
    assert second['ok'] is False
    assert second['code']=='REPAIR_LOOP_DETECTED'


def test_repair_guard_allows_state_change_but_caps_total_attempts():
    guard=knowledge_registry.RepairLoopGuard(max_attempts=2)
    assert guard.check('rule.a','state-1')['ok'] is True
    assert guard.check('rule.a','state-2')['ok'] is True
    third=guard.check('rule.a','state-3')
    assert third['ok'] is False
    assert third['code']=='RECIPE_RETRY_LIMIT'
