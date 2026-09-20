from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ADAPTER_PY=ROOT/"houdini_adapter"/"python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0,str(ADAPTER_PY))

import ai_bridge_houdini.client as client_mod
import ai_bridge_houdini.dispatcher as dispatcher_mod


def _session():
    return {"session_id":"HOU-TEST","capabilities":[]}


def test_recipe_get_accepts_recipe_id_alias(monkeypatch):
    monkeypatch.setattr(dispatcher_mod.knowledge_registry,"get_recipe",lambda name:{"id":name})
    command={
        "command_id":"c1",
        "operation":"recipe.get",
        "arguments":{"recipe_id":"network.build"},
    }
    result=dispatcher_mod.dispatch(None,command,_session())
    assert result["status"]=="success"
    assert result["result"]["id"]=="network.build"


def test_missing_recipe_argument_returns_structured_failure_even_if_logging_is_broken(monkeypatch):
    def broken_log(message):
        raise RuntimeError("logger unavailable")
    monkeypatch.setattr(dispatcher_mod.local_log,"exception",broken_log)
    command={
        "command_id":"c2",
        "operation":"recipe.get",
        "arguments":{},
    }
    result=dispatcher_mod.dispatch(None,command,_session())
    assert result["status"]=="failed"
    assert result["failure"]["code"]=="ARGUMENT_REQUIRED"
    assert result["failure"]["retryable"] is False


def test_client_safe_dispatch_contains_unexpected_dispatcher_exception(monkeypatch):
    def boom(hou,command,session_info):
        raise RuntimeError("unexpected dispatch failure")
    monkeypatch.setattr(client_mod,"dispatch",boom)
    result=client_mod._safe_dispatch(
        None,
        {"command_id":"c3","operation":"inspect.context","arguments":{}},
        {"session_id":"HOU-TEST"},
    )
    assert result["status"]=="failed"
    assert result["failure"]["code"]=="ADAPTER_DISPATCH_EXCEPTION"
    assert result["failure"]["retryable"] is False
    assert result["last_known_state"]["session"]=="HOU-TEST"


def test_result_preserves_structured_recipe_failure():
    payload={
        'verified':False,
        'failure':{
            'code':'RECIPE_STEP_FAILED',
            'category':'recipe',
            'message':'NODE_NOT_FOUND: /obj/missing',
            'retryable':False,
        },
    }
    result=dispatcher_mod._result('c4','HOU-TEST',payload)
    assert result['status']=='failed'
    assert result['stages']=={'EXECUTE':'FAILED'}
    assert result['failure']['code']=='RECIPE_STEP_FAILED'
    assert result['failure']['message'].startswith('NODE_NOT_FOUND')
    assert result['failure']['underlying']['category']=='recipe'


def test_recipe_apply_denies_unpromoted_execution(monkeypatch):
    monkeypatch.setattr(dispatcher_mod.knowledge_registry,'recipe_promotion_state',lambda recipe_id:'unregistered')
    command={
        'command_id':'c5',
        'operation':'recipe.apply',
        'arguments':{'recipe':'code.safe_patch_and_cook','values':{}},
    }
    result=dispatcher_mod.dispatch(None,command,_session())
    assert result['status']=='denied'
    assert result['failure']['code']=='RECIPE_NOT_PROMOTED'
    assert result['stages']=={'EXECUTE':'NOT_RUN'}
