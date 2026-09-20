import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
ADAPTER_PY=ROOT/'houdini_adapter'/'python'
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0,str(ADAPTER_PY))

from ai_bridge_houdini import knowledge_registry


def _write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload),encoding='utf-8')


def _env(tmp_path,monkeypatch,recipe):
    recipes=tmp_path/'recipes'
    errors=tmp_path/'error_catalog.json'
    rules=tmp_path/'host_rules.json'
    _write(recipes/(recipe['id']+'.json'),recipe)
    _write(errors,{'rules':[]})
    _write(rules,{'rules':[]})
    monkeypatch.setattr(knowledge_registry,'RECIPES_DIR',recipes)
    monkeypatch.setattr(knowledge_registry,'ERROR_CATALOG',errors)
    monkeypatch.setattr(knowledge_registry,'HOST_RULES',rules)


def test_recipe_rejects_step_budget_overflow(tmp_path,monkeypatch):
    recipe={'id':'demo.too_many','version':'1','required':[],'steps':[{'op':'node.state','arguments':{'path':'/obj/x'}} for _ in range(65)]}
    _env(tmp_path,monkeypatch,recipe)
    result=knowledge_registry.validate_recipe('demo.too_many',{},hou=None)
    assert result['ok'] is False
    assert any(e.get('code')=='RECIPE_BUDGET_EXCEEDED' for e in result['errors'])


def test_recipe_rejects_recipe_recursion(tmp_path,monkeypatch):
    recipe={'id':'demo.loop','version':'1','required':[],'steps':[{'op':'recipe.apply','arguments':{'recipe':'demo.loop'}}]}
    _env(tmp_path,monkeypatch,recipe)
    result=knowledge_registry.validate_recipe('demo.loop',{},hou=None)
    assert result['ok'] is False
    assert any(e.get('code')=='RECIPE_CYCLE_DETECTED' for e in result['errors'])


def test_recipe_primitive_exception_returns_structured_failure(tmp_path,monkeypatch):
    recipe={'id':'demo.fail','version':'1','required':[],'steps':[{'op':'node.state','save_as':'state','arguments':{'path':'/obj/missing'}}]}
    _env(tmp_path,monkeypatch,recipe)
    def explode(hou,op,args):
        raise ValueError('Node not found: /obj/missing')
    monkeypatch.setattr(knowledge_registry,'_execute_primitive',explode)
    result=knowledge_registry.apply_recipe(object(),'demo.fail',{})
    assert result['verified'] is False
    assert result['failure']['code']=='RECIPE_STEP_FAILED'
    assert result['failure']['step_index']==0
    assert result['failure']['operation']=='node.state'
    assert result['trace'][0]['status']=='FAILED'


def test_recipe_never_retries_failed_primitive_implicitly(tmp_path,monkeypatch):
    recipe={'id':'demo.no_retry','version':'1','required':[],'steps':[{'op':'node.state','arguments':{'path':'/obj/x'}}]}
    _env(tmp_path,monkeypatch,recipe)
    calls={'n':0}
    def explode(hou,op,args):
        calls['n']+=1
        raise RuntimeError('boom')
    monkeypatch.setattr(knowledge_registry,'_execute_primitive',explode)
    result=knowledge_registry.apply_recipe(object(),'demo.no_retry',{})
    assert calls['n']==1
    assert result['failure']['code']=='RECIPE_STEP_FAILED'


def test_recipe_validate_defers_runtime_result_condition(tmp_path,monkeypatch):
    recipe={
        'id':'demo.deferred','version':'1','required':['path'],
        'steps':[
            {'op':'cook.execute','save_as':'cook','arguments':{'path':{'$arg':'path'},'force':True}},
            {'op':'host.errors','save_as':'errors','when':{'$result_equals':{'path':'cook.cook_status','value':'FAILED'}},'arguments':{'path':{'$arg':'path'}}},
        ],
    }
    _env(tmp_path,monkeypatch,recipe)
    result=knowledge_registry.validate_recipe('demo.deferred',{'path':'/obj/x'},hou=None)
    assert result['ok'] is True
    assert [item['op'] for item in result['resolved_steps']] == ['cook.execute','host.errors']


def test_recipe_semantic_validation_rejects_forward_result_reference():
    recipe={
        'id':'demo.forward','version':'1','required':[],
        'steps':[
            {'op':'parm.write','arguments':{'path':'/obj/x','parameter':'p','value':1,'expected_hash':{'$result':'later.hash'}}},
            {'op':'parm.read','save_as':'later','arguments':{'path':'/obj/x','parameter':'p'}},
        ],
    }
    with pytest.raises(knowledge_registry.KnowledgeError, match='non-prior result'):
        knowledge_registry._validate_recipe_semantics(recipe)


def test_recipe_cook_exception_can_continue_to_conditional_host_errors(monkeypatch):
    calls = []

    def fake_primitive(hou, op, args):
        calls.append(op)
        if op == "code.read":
            return {"text": "old", "hash": "hash-old"}
        if op == "code.patch":
            return {"verified": True, "after": {"hash": "hash-new"}}
        if op == "cook.execute":
            raise RuntimeError("synthetic cook exception")
        if op == "host.errors":
            return {"errors": ["compile failed"], "warnings": []}
        raise AssertionError(op)

    monkeypatch.setattr(knowledge_registry, "_execute_primitive", fake_primitive)
    result = knowledge_registry.apply_recipe(
        object(),
        "code.safe_patch_and_cook",
        {
            "path": "/obj/geo1/wrangle1",
            "parameter": "snippet",
            "old_text": "old",
            "new_text": "bad",
            "cook_path": "/obj/geo1/wrangle1",
        },
    )

    assert calls == ["code.read", "code.patch", "cook.execute", "host.errors"]
    assert result["verified"] is False
    assert result["failure"]["operation"] == "cook.execute"
    assert result["failure"]["exception_type"] == "RuntimeError"
    assert result["results"]["cook"]["cook_status"] == "FAILED"
    assert result["results"]["errors"]["errors"] == ["compile failed"]
    assert [row["status"] for row in result["trace"]] == ["PASS", "PASS", "FAILED", "PASS"]
