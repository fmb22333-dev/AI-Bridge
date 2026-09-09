from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import capability_guidance, code_ops, compat_ops, cook_ops, graph_ops, node_ops, parm_ops


KNOWLEDGE_ROOT = Path(__file__).resolve().parent / "knowledge"
RECIPES_DIR = KNOWLEDGE_ROOT / "recipes"
ERROR_CATALOG = KNOWLEDGE_ROOT / "error_catalog.json"
HOST_RULES = KNOWLEDGE_ROOT / "host_rules.json"
PROMOTION_REGISTRY = KNOWLEDGE_ROOT / "promotion_registry.json"
TEMPLATE_CATALOG = KNOWLEDGE_ROOT / "template_catalog.json"
ALIAS_CATALOG = KNOWLEDGE_ROOT / "alias_catalog.json"
CAPABILITY_GUIDANCE = capability_guidance.GUIDANCE_PATH


class KnowledgeError(RuntimeError):
    pass


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _all_recipe_files() -> list[Path]:
    if not RECIPES_DIR.exists():
        return []
    return sorted(p for p in RECIPES_DIR.glob("*.json") if p.is_file())


def _digest_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        h.update(path.name.encode("utf-8"))
        try:
            h.update(path.read_bytes())
        except FileNotFoundError:
            continue
    return h.hexdigest()


def _recipe_payload(path: Path) -> dict:
    data = _json(path, None)
    if not isinstance(data, dict):
        raise KnowledgeError(f"Recipe must be a JSON object: {path.name}")
    if not data.get("id"):
        raise KnowledgeError(f"Recipe id missing: {path.name}")
    if not isinstance(data.get("steps"), list):
        raise KnowledgeError(f"Recipe steps missing: {data.get('id')}")
    return data


def recipes() -> list[dict]:
    out = []
    for path in _all_recipe_files():
        data = _recipe_payload(path)
        promotion_state = recipe_promotion_state(data["id"])
        out.append(
            {
                "id": data["id"],
                "version": str(data.get("version") or "1.0"),
                "description": str(data.get("description") or ""),
                "required": list(data.get("required") or []),
                "optional": list(data.get("optional") or []),
                "file": path.name,
                "promotion_state": promotion_state,
                "execution_authorized": promotion_state == "promoted",
            }
        )
    return out


def recipe_promotion_entry(recipe_id: str) -> dict | None:
    recipe_id = str(recipe_id or "").strip()
    target = f"recipe:{recipe_id}"
    entry_id = f"recipe.{recipe_id}"
    for item in promotion_entries():
        if str(item.get("kind") or "") != "recipe":
            continue
        if str(item.get("target") or "") == target or str(item.get("id") or "") == entry_id:
            return dict(item)
    return None


def get_recipe(recipe_id: str) -> dict:
    for path in _all_recipe_files():
        data = _recipe_payload(path)
        if data["id"] == recipe_id:
            out = dict(data)
            entry = recipe_promotion_entry(recipe_id)
            state = str((entry or {}).get("state") or "unregistered").strip().lower()
            out["promotion_state"] = state
            out["execution_authorized"] = state == "promoted"
            if entry and entry.get("superseded_by"):
                out["superseded_by"] = entry.get("superseded_by")
            if entry and entry.get("deprecation_reason"):
                out["deprecation_reason"] = entry.get("deprecation_reason")
            return out
    raise KnowledgeError(f"Recipe not found: {recipe_id}")


def error_rules() -> list[dict]:
    data = _json(ERROR_CATALOG, {"rules": []})
    rules = data.get("rules") if isinstance(data, dict) else []
    return [rule for rule in rules or [] if isinstance(rule, dict)]


def host_rules() -> list[dict]:
    data = _json(HOST_RULES, {"rules": []})
    rules = data.get("rules") if isinstance(data, dict) else []
    return [rule for rule in rules or [] if isinstance(rule, dict)]


def _catalog_items(path: Path, key: str) -> list[dict]:
    data = _json(path, {key: []})
    if not isinstance(data, dict):
        raise KnowledgeError(f"KNOWLEDGE_VALIDATION_FAILED: {path.name} must be an object")
    items = data.get(key, [])
    if not isinstance(items, list):
        raise KnowledgeError(f"KNOWLEDGE_VALIDATION_FAILED: {path.name}.{key} must be a list")
    return [item for item in items if isinstance(item, dict)]


def promotion_entries() -> list[dict]:
    return _catalog_items(PROMOTION_REGISTRY, "entries")


def recipe_promotion_state(recipe_id: str) -> str:
    recipe_id = str(recipe_id or "").strip()
    target = f"recipe:{recipe_id}"
    entry_id = f"recipe.{recipe_id}"
    for item in promotion_entries():
        if str(item.get("kind") or "") != "recipe":
            continue
        if str(item.get("target") or "") == target or str(item.get("id") or "") == entry_id:
            return str(item.get("state") or "unregistered").strip().lower()
    return "unregistered"


def recipe_execution_authorized(recipe_id: str) -> bool:
    return recipe_promotion_state(recipe_id) == "promoted"


def templates() -> list[dict]:
    return _catalog_items(TEMPLATE_CATALOG, "templates")


def alias_sets() -> list[dict]:
    return _catalog_items(ALIAS_CATALOG, "alias_sets")


def capability_guidance_entries() -> list[dict]:
    return capability_guidance.catalog(promoted_only=False)["entries"]


def status() -> dict:
    paths = _all_recipe_files() + [
        ERROR_CATALOG, HOST_RULES, PROMOTION_REGISTRY, TEMPLATE_CATALOG, ALIAS_CATALOG, CAPABILITY_GUIDANCE,
    ]
    existing = [p for p in paths if p.exists()]
    latest = max((p.stat().st_mtime_ns for p in existing), default=0)
    lifecycle = promotion_entries()
    recipe_items = recipes()
    template_items = templates()
    alias_items = alias_sets()
    guidance_status = capability_guidance.status()
    return {
        "knowledge_root": str(KNOWLEDGE_ROOT),
        "recipe_count": len(recipe_items),
        "recipe_promoted_count": sum(1 for item in recipe_items if item.get("promotion_state") == "promoted"),
        "recipe_unpromoted_count": sum(1 for item in recipe_items if item.get("promotion_state") != "promoted"),
        "error_rule_count": len(error_rules()),
        "host_rule_count": len(host_rules()),
        "promotion_entry_count": len(lifecycle),
        "observed_count": sum(1 for item in lifecycle if item.get("state") == "observed"),
        "candidate_count": sum(1 for item in lifecycle if item.get("state") == "candidate"),
        "validated_count": sum(1 for item in lifecycle if item.get("state") == "validated"),
        "promoted_count": sum(1 for item in lifecycle if item.get("state") == "promoted"),
        "deprecated_count": sum(1 for item in lifecycle if item.get("state") == "deprecated"),
        "template_count": len(template_items),
        "alias_set_count": len(alias_items),
        "capability_guidance_count": guidance_status["entry_count"],
        "capability_guidance_promoted_count": guidance_status["promoted_count"],
        "capability_guidance_candidate_count": guidance_status["candidate_count"],
        "capability_guidance": guidance_status,
        "content_digest": _digest_files(existing),
        "latest_mtime_ns": latest,
        "hot_reload": True,
        "restart_required_for_knowledge_changes": False,
    }


def _search_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\W_]+", str(value or "").lower()) if token]


def _search_match(query: str, hay: str) -> dict | None:
    query = str(query or "").strip().lower()
    hay = str(hay or "").lower()
    if query and query in hay:
        return {"mode": "exact_substring", "score": 2000 + len(query)}

    tokens = _search_tokens(query)
    if not tokens:
        return None
    hay_tokens = set(_search_tokens(hay))
    if all(token in hay_tokens for token in tokens):
        return {"mode": "all_tokens", "score": 1000 + len(tokens)}
    return None


def search(query: str, limit: int = 20) -> dict:
    query = str(query or "").strip().lower()
    if not query:
        raise KnowledgeError("query is required")
    try:
        limit = max(1, min(int(limit), 100))
    except Exception:
        limit = 20

    hits = []

    def add(kind: str, item: dict, *, promotion_entry: bool = False):
        hay = json.dumps(item, ensure_ascii=False).lower()
        match = _search_match(query, hay)
        if match is None:
            return
        if promotion_entry:
            hit = dict(item)
            hit["entry_kind"] = hit.pop("kind", None)
            hit["kind"] = "promotion_entry"
        else:
            hit = {"kind": kind, **item}
        hit["_search"] = match
        hits.append(hit)

    for item in recipes():
        add("recipe", item)
    for rule in error_rules():
        add("error_rule", rule)
    for rule in host_rules():
        add("host_rule", rule)
    for item in promotion_entries():
        add("promotion_entry", item, promotion_entry=True)
    for item in templates():
        add("template", item)
    for item in alias_sets():
        add("alias_set", item)
    for item in capability_guidance_entries():
        guidance_item = dict(item)
        guidance_item["guidance_kind"] = guidance_item.pop("kind", None)
        add("capability_guidance", guidance_item)

    hits.sort(key=lambda item: item.get("_search", {}).get("score", 0), reverse=True)
    selected = hits[:limit]
    return {
        "query": query,
        "count": len(selected),
        "total_matches": len(hits),
        "matching": ["exact_substring", "all_tokens"],
        "results": selected,
    }


def match_error_text(text: str) -> dict | None:
    value = str(text or "")
    upper = value.upper()
    for rule in error_rules():
        all_tokens = [str(x).upper() for x in rule.get("contains_all") or []]
        any_tokens = [str(x).upper() for x in rule.get("contains_any") or []]
        excludes = [str(x).upper() for x in rule.get("exclude") or []]
        if excludes and any(token in upper for token in excludes):
            continue
        if all_tokens and not all(token in upper for token in all_tokens):
            continue
        if any_tokens and not any(token in upper for token in any_tokens):
            continue
        if not all_tokens and not any_tokens:
            continue
        return dict(rule)
    return None


def _resolve_value(value: Any, values: dict, results: dict, *, allow_deferred: bool = False):
    if isinstance(value, dict):
        if set(value) == {"$arg"}:
            key = str(value["$arg"])
            if key not in values:
                raise KnowledgeError(f"Missing recipe argument: {key}")
            return values[key]
        if set(value) == {"$arg_optional"}:
            key = str(value["$arg_optional"])
            return values.get(key)
        if set(value) == {"$result"}:
            path_text = str(value["$result"])
            path = path_text.split(".")
            if not path or path[0] not in results:
                if allow_deferred:
                    return {"$deferred_result": path_text}
                raise KnowledgeError(f"Unknown recipe result reference: {path_text}")
            current: Any = results[path[0]]
            for part in path[1:]:
                if not isinstance(current, dict) or part not in current:
                    if allow_deferred:
                        return {"$deferred_result": path_text}
                    raise KnowledgeError(f"Invalid recipe result path: {path_text}")
                current = current[part]
            return current
        return {k: _resolve_value(v, values, results, allow_deferred=allow_deferred) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, values, results, allow_deferred=allow_deferred) for v in value]
    return value


def _condition(step: dict, values: dict, results: dict, *, allow_deferred: bool = False) -> bool:
    when = step.get("when")
    if when is None:
        return True
    if isinstance(when, dict) and "$present" in when:
        return str(when["$present"]) in values and values.get(str(when["$present"])) not in (None, "")
    if isinstance(when, dict) and "$result_equals" in when:
        spec = when["$result_equals"]
        if not isinstance(spec, dict) or not str(spec.get("path") or "").strip():
            raise KnowledgeError(f"Invalid recipe result condition: {when}")
        path_text = str(spec["path"])
        actual = _resolve_value(
            {"$result": path_text},
            values,
            results,
            allow_deferred=allow_deferred,
        )
        if allow_deferred and isinstance(actual, dict) and actual.get("$deferred_result") == path_text:
            return True
        return actual == spec.get("value")
    raise KnowledgeError(f"Unsupported recipe condition: {when}")


def validate_recipe(recipe_id: str, values: dict, hou=None) -> dict:
    recipe = get_recipe(recipe_id)
    missing = [name for name in recipe.get("required") or [] if name not in values]
    errors = [{"code": "RECIPE_ARGUMENT_REQUIRED", "argument": name} for name in missing]
    resolved_steps = []
    results = {}

    if not errors:
        for index, step in enumerate(recipe["steps"]):
            try:
                if not _condition(step, values, results, allow_deferred=True):
                    continue
                args = _resolve_value(step.get("arguments") or {}, values, results, allow_deferred=True)
                resolved_steps.append({"index": index, "op": step.get("op"), "arguments": args})
            except Exception as exc:
                errors.append({"code": "RECIPE_RESOLUTION_FAILED", "index": index, "message": str(exc)})
                break

    preflight = None
    if hou is not None and not errors:
        for step in resolved_steps:
            if step["op"] == "network.validate":
                args = step["arguments"]
                preflight = graph_ops.validate_spec(
                    hou,
                    args["parent"],
                    args.get("nodes", []),
                    args.get("connections", []),
                )
                if not preflight.get("ok"):
                    errors.extend(preflight.get("errors") or [])
                break

    return {
        "ok": not errors,
        "recipe": recipe_id,
        "version": str(recipe.get("version") or "1.0"),
        "errors": errors,
        "resolved_steps": resolved_steps,
        "preflight": preflight,
    }


def _execute_primitive(hou, op: str, args: dict):
    if op == "network.validate":
        return graph_ops.validate_spec(hou, args["parent"], args.get("nodes", []), args.get("connections", []))
    if op == "network.apply":
        return graph_ops.apply_spec(
            hou,
            args["parent"],
            args.get("nodes", []),
            args.get("connections", []),
            layout=bool(args.get("layout", False)),
            allow_update_existing=bool(args.get("allow_update_existing", False)),
        )
    if op == "network.apply_transactional":
        return graph_ops.apply_transactional(
            hou,
            args["parent"],
            args.get("nodes", []),
            args.get("connections", []),
            layout=bool(args.get("layout", False)),
        )
    if op == "network.ensure_plan":
        return graph_ops.plan_ensure(
            hou,
            args["parent"],
            args.get("nodes", []),
            args.get("connections", []),
            cook_path=args.get("cook_path"),
            max_details=int(args.get("max_details", 200)),
        )
    if op == "network.ensure_transactional":
        return graph_ops.ensure_transactional(
            hou,
            args["parent"],
            args.get("nodes", []),
            args.get("connections", []),
            layout=bool(args.get("layout", False)),
            cook_path=args.get("cook_path"),
            force=bool(args.get("force", True)),
            expected_plan_hash=args.get("expected_plan_hash"),
        )
    if op == "cook.execute":
        return cook_ops.cook(hou, args["path"], args.get("force", True))
    if op == "host.errors":
        return cook_ops.host_errors(hou, args["path"])
    if op == "code.read":
        return code_ops.read(hou, args["path"], args["parameter"])
    if op == "code.patch":
        return code_ops.patch(
            hou,
            args["path"],
            args["parameter"],
            args["old_text"],
            args["new_text"],
            args.get("expected_hash"),
        )
    if op == "parm.read":
        return parm_ops.read(hou, args["path"], args["parameter"])
    if op == "parm.write":
        return parm_ops.write(hou, args["path"], args["parameter"], args["value"], args.get("expected_hash"))
    if op == "node.input_state":
        return node_ops.input_state(hou, args["target"], args["input_index"])
    if op == "node.batch_connect":
        return node_ops.batch_connect(hou, args["items"])
    if op == "node.state":
        return compat_ops.node_state(hou, args["path"])
    if op == "node.set_state":
        return compat_ops.node_set_state(hou, args["path"], args.get("values", {}))
    raise KnowledgeError(f"Recipe primitive not allowed: {op}")


def apply_recipe(hou, recipe_id: str, values: dict) -> dict:
    check = validate_recipe(recipe_id, values, hou=hou)
    if not check["ok"]:
        raise KnowledgeError(f"Recipe validation failed: {check['errors']}")

    recipe = get_recipe(recipe_id)
    results: dict[str, dict] = {}
    trace = []

    for index, step in enumerate(recipe["steps"]):
        if not _condition(step, values, results):
            trace.append({"index": index, "op": step.get("op"), "status": "SKIPPED"})
            continue
        op = str(step.get("op") or "")
        args = _resolve_value(step.get("arguments") or {}, values, results)
        payload = _execute_primitive(hou, op, args)
        key = str(step.get("save_as") or f"step_{index}")
        results[key] = payload
        status_value = "PASS"
        if op == "network.validate" and isinstance(payload, dict) and not payload.get("ok", False):
            status_value = "FAILED"
        if op == "cook.execute" and isinstance(payload, dict) and payload.get("cook_status") == "FAILED":
            status_value = "FAILED"
        trace.append({"index": index, "op": op, "save_as": key, "status": status_value})
        if status_value == "FAILED" and bool(step.get("stop_on_failure", True)):
            break

    return {
        "recipe": recipe_id,
        "version": str(recipe.get("version") or "1.0"),
        "trace": trace,
        "results": results,
        "knowledge_digest": status()["content_digest"],
        "verified": all(item["status"] != "FAILED" for item in trace),
    }


# AI_BRIDGE_P0_RECIPE_SAFETY_V1
_RECIPE_MAX_STEPS = 64
_RECIPE_MAX_SECONDS = 30.0
_RECIPE_ALLOWED_PRIMITIVES = {
    'network.validate','network.apply','network.apply_transactional','network.ensure_plan','network.ensure_transactional','cook.execute','host.errors',
    'code.read','code.patch','parm.read','parm.write',
    'node.input_state','node.batch_connect','node.state','node.set_state',
}
_unsafe_validate_recipe = validate_recipe

def validate_recipe(recipe_id: str, values: dict, hou=None) -> dict:
    try:
        recipe = get_recipe(recipe_id)
    except Exception as exc:
        message = str(exc)
        code = 'RECIPE_CYCLE_DETECTED' if 'RECIPE_CYCLE_DETECTED' in message else 'RECIPE_VALIDATION_FAILED'
        return {
            'ok': False, 'recipe': recipe_id, 'version': None,
            'errors': [{'code':code,'message':message}],
            'resolved_steps': [], 'preflight': None,
        }

    errors = []
    steps = recipe.get('steps') or []
    if not isinstance(steps, list):
        errors.append({'code':'RECIPE_VALIDATION_FAILED','message':'steps must be a list'})
        steps = []
    if len(steps) > _RECIPE_MAX_STEPS:
        errors.append({
            'code':'RECIPE_BUDGET_EXCEEDED',
            'message':f'Recipe has {len(steps)} steps; maximum is {_RECIPE_MAX_STEPS}',
            'limit':_RECIPE_MAX_STEPS,
            'actual':len(steps),
        })
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append({'code':'RECIPE_VALIDATION_FAILED','index':index,'message':'step must be an object'})
            continue
        op = str(step.get('op') or '')
        if op.startswith('recipe.'):
            errors.append({
                'code':'RECIPE_CYCLE_DETECTED',
                'index':index,
                'operation':op,
                'message':'Recipe-to-recipe invocation is disabled in P0',
            })
        elif op not in _RECIPE_ALLOWED_PRIMITIVES:
            errors.append({
                'code':'RECIPE_CAPABILITY_VIOLATION',
                'index':index,
                'operation':op,
                'message':f'Recipe primitive not allowed: {op}',
            })
        if any(key in step for key in ('while','repeat','foreach','retry','retries')):
            errors.append({
                'code':'RECIPE_VALIDATION_FAILED',
                'index':index,
                'message':'Unbounded loop/retry constructs are not allowed',
            })

    try:
        base = _unsafe_validate_recipe(recipe_id, values, hou=hou)
    except Exception as exc:
        base = {
            'ok':False,'recipe':recipe_id,'version':str(recipe.get('version') or '1.0'),
            'errors':[{'code':'RECIPE_VALIDATION_FAILED','message':str(exc)}],
            'resolved_steps':[],'preflight':None,
        }
    merged = list(errors) + list(base.get('errors') or [])
    base['errors'] = merged
    base['ok'] = not merged
    base['safety'] = {
        'max_steps':_RECIPE_MAX_STEPS,
        'max_seconds':_RECIPE_MAX_SECONDS,
        'implicit_retries':0,
        'recipe_composition':False,
    }
    return base

def _recipe_failure(recipe_id, recipe, trace, *, code, message, step_index=None, operation=None, exception_type=None, underlying=None):
    failure = {
        'code':code,
        'category':'recipe',
        'message':message,
        'recipe':recipe_id,
        'recipe_version':str(recipe.get('version') or '1.0'),
        'step_index':step_index,
        'operation':operation,
        'exception_type':exception_type,
        'retryable':False,
    }
    if underlying is not None:
        failure['underlying'] = underlying
    return {
        'recipe':recipe_id,
        'version':str(recipe.get('version') or '1.0'),
        'trace':trace,
        'results':{},
        'knowledge_digest':status()['content_digest'],
        'verified':False,
        'failure':failure,
    }

def apply_recipe(hou, recipe_id: str, values: dict) -> dict:
    import time as _time
    check = validate_recipe(recipe_id, values, hou=hou)
    recipe = get_recipe(recipe_id)
    if not check['ok']:
        return _recipe_failure(
            recipe_id, recipe, [],
            code='RECIPE_VALIDATION_FAILED',
            message='Recipe validation failed',
            underlying=check['errors'],
        )

    results = {}
    trace = []
    first_failure = None
    started = _time.monotonic()
    budget = recipe.get('max_seconds', _RECIPE_MAX_SECONDS)
    try:
        budget = min(_RECIPE_MAX_SECONDS, max(0.1, float(budget)))
    except Exception:
        budget = _RECIPE_MAX_SECONDS

    for index, step in enumerate(recipe['steps']):
        elapsed = _time.monotonic() - started
        if elapsed > budget:
            failure = {
                'code':'RECIPE_BUDGET_EXCEEDED','category':'recipe','message':f'Recipe exceeded {budget:.3f}s budget',
                'recipe':recipe_id,'recipe_version':str(recipe.get('version') or '1.0'),'step_index':index,
                'operation':str(step.get('op') or ''),'exception_type':None,'retryable':False,
            }
            trace.append({'index':index,'op':step.get('op'),'status':'FAILED','failure_code':'RECIPE_BUDGET_EXCEEDED'})
            return {
                'recipe':recipe_id,'version':str(recipe.get('version') or '1.0'),'trace':trace,'results':results,
                'knowledge_digest':status()['content_digest'],'verified':False,'failure':failure,
            }
        if not _condition(step, values, results):
            trace.append({'index':index,'op':step.get('op'),'status':'SKIPPED'})
            continue
        op = str(step.get('op') or '')
        key = str(step.get('save_as') or f'step_{index}')
        step_started = _time.monotonic()
        try:
            args = _resolve_value(step.get('arguments') or {}, values, results)
            payload = _execute_primitive(hou, op, args)
            results[key] = payload
            status_value = 'PASS'
            underlying = None
            if op == 'network.validate' and isinstance(payload, dict) and not payload.get('ok', False):
                status_value = 'FAILED'; underlying = payload.get('errors')
            if op == 'cook.execute' and isinstance(payload, dict) and payload.get('cook_status') == 'FAILED':
                status_value = 'FAILED'; underlying = payload.get('errors')
            if isinstance(payload, dict) and payload.get('conflict'):
                status_value = 'FAILED'; underlying = payload
            if isinstance(payload, dict) and payload.get('verified') is False:
                status_value = 'FAILED'; underlying = payload
            trace.append({
                'index':index,'op':op,'save_as':key,'status':status_value,
                'duration_ms':round((_time.monotonic()-step_started)*1000.0,3),
                'failure_code':'RECIPE_STEP_FAILED' if status_value == 'FAILED' else None,
            })
            if status_value == 'FAILED':
                failure = {
                    'code':'RECIPE_STEP_FAILED','category':'recipe','message':f'Recipe step {index} failed',
                    'recipe':recipe_id,'recipe_version':str(recipe.get('version') or '1.0'),'step_index':index,
                    'operation':op,'exception_type':None,'retryable':False,'underlying':underlying,
                }
                if first_failure is None:
                    first_failure = failure
                if bool(step.get('stop_on_failure', True)):
                    break
        except Exception as exc:
            failure = {
                'code':'RECIPE_STEP_FAILED','category':'recipe','message':str(exc),
                'recipe':recipe_id,'recipe_version':str(recipe.get('version') or '1.0'),'step_index':index,
                'operation':op,'exception_type':type(exc).__name__,'retryable':False,
            }
            # Preserve a machine-readable failed step result so later bounded
            # diagnostic conditions can still resolve after a Host primitive
            # raises instead of returning a normal FAILED payload. In
            # particular, code.safe_patch_and_cook needs cook.cook_status to
            # resolve to FAILED so host.errors can run.
            exception_payload = {
                'failed': True,
                'exception_type': type(exc).__name__,
                'message': str(exc),
            }
            if op == 'cook.execute':
                exception_payload['cook_status'] = 'FAILED'
            results[key] = exception_payload
            trace.append({
                'index':index,'op':op,'save_as':key,'status':'FAILED',
                'duration_ms':round((_time.monotonic()-step_started)*1000.0,3),
                'failure_code':'RECIPE_STEP_FAILED',
            })
            if first_failure is None:
                first_failure = failure
            if bool(step.get('stop_on_failure', True)):
                break

    return {
        'recipe':recipe_id,
        'version':str(recipe.get('version') or '1.0'),
        'trace':trace,
        'results':results,
        'knowledge_digest':status()['content_digest'],
        'verified':first_failure is None,
        'failure':first_failure,
        'safety':{'step_budget':_RECIPE_MAX_STEPS,'time_budget_seconds':budget,'implicit_retries':0},
    }


# AI_BRIDGE_P0_KNOWLEDGE_ATOMIC_V1
import copy as _p0_copy
_p0_unsafe_json = _json
_p0_unsafe_recipes = recipes
_p0_unsafe_status = status
_p0_json_last_good = {}
_p0_recipe_sets = {}
_p0_validation_errors = {}

def _p0_path_key(path):
    try:
        return str(path.resolve())
    except Exception:
        return str(path)

def _p0_validate_loaded(path, data):
    key = _p0_path_key(path)
    if path == ERROR_CATALOG or path == HOST_RULES:
        if not isinstance(data, dict) or not isinstance(data.get('rules', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: rules document must contain a list')
    if path == PROMOTION_REGISTRY:
        if not isinstance(data, dict) or not isinstance(data.get('entries', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: promotion registry must contain entries list')
    if path == TEMPLATE_CATALOG:
        if not isinstance(data, dict) or not isinstance(data.get('templates', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: template catalog must contain templates list')
    if path == ALIAS_CATALOG:
        if not isinstance(data, dict) or not isinstance(data.get('alias_sets', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: alias catalog must contain alias_sets list')
    try:
        is_recipe = path.parent == RECIPES_DIR and path.suffix.lower() == '.json'
    except Exception:
        is_recipe = False
    if is_recipe:
        if not isinstance(data, dict):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe must be an object')
        if not str(data.get('id') or '').strip():
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe id required')
        steps = data.get('steps', [])
        if not isinstance(steps, list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe steps must be a list')
        if len(steps) > _RECIPE_MAX_STEPS:
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe exceeds step budget')
    return data

def _json(path, default=None):
    key = _p0_path_key(path)
    try:
        data = _p0_unsafe_json(path, default)
        data = _p0_validate_loaded(path, data)
        _p0_json_last_good[key] = _p0_copy.deepcopy(data)
        _p0_validation_errors.pop(key, None)
        return data
    except Exception as exc:
        _p0_validation_errors[key] = {
            'code':'KNOWLEDGE_VALIDATION_FAILED',
            'path':key,
            'message':str(exc),
        }
        if key in _p0_json_last_good:
            return _p0_copy.deepcopy(_p0_json_last_good[key])
        raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: {key}: {exc}') from exc

def recipes() -> list[dict]:
    root_key = _p0_path_key(RECIPES_DIR)
    try:
        items = _p0_unsafe_recipes()
        _p0_recipe_sets[root_key] = _p0_copy.deepcopy(items)
        return items
    except Exception as exc:
        _p0_validation_errors[root_key] = {
            'code':'KNOWLEDGE_VALIDATION_FAILED',
            'path':root_key,
            'message':str(exc),
        }
        if root_key in _p0_recipe_sets:
            return _p0_copy.deepcopy(_p0_recipe_sets[root_key])
        raise

def status() -> dict:
    data = _p0_unsafe_status()
    active_paths = _all_recipe_files() + [
        ERROR_CATALOG, HOST_RULES, PROMOTION_REGISTRY, TEMPLATE_CATALOG, ALIAS_CATALOG, CAPABILITY_GUIDANCE,
    ]
    active_keys = {_p0_path_key(path) for path in active_paths}
    active_keys.add(_p0_path_key(RECIPES_DIR))
    errors = [
        value for key, value in _p0_validation_errors.items()
        if key in active_keys
    ]
    guidance = data.get('capability_guidance') or {}
    if guidance.get('degraded') and guidance.get('validation_error'):
        errors.append(guidance['validation_error'])
    data['knowledge_degraded'] = bool(errors)
    data['validation_errors'] = errors
    data['activation_mode'] = 'last_known_good'
    return data

class RepairLoopGuard:
    def __init__(self, max_attempts=3):
        value = int(max_attempts)
        if value < 1:
            raise ValueError('max_attempts must be >= 1')
        self.max_attempts = min(value, 16)
        self._seen = set()
        self._attempts = 0

    def check(self, rule_id, state_fingerprint):
        key = (str(rule_id), str(state_fingerprint))
        if key in self._seen:
            return {
                'ok':False,'code':'REPAIR_LOOP_DETECTED','retryable':False,
                'rule_id':key[0],'state_fingerprint':key[1],'attempts':self._attempts,
            }
        if self._attempts >= self.max_attempts:
            return {
                'ok':False,'code':'RECIPE_RETRY_LIMIT','retryable':False,
                'rule_id':key[0],'state_fingerprint':key[1],'attempts':self._attempts,
            }
        self._seen.add(key)
        self._attempts += 1
        return {
            'ok':True,'code':None,'retryable':True,
            'rule_id':key[0],'state_fingerprint':key[1],'attempts':self._attempts,
        }


# AI_BRIDGE_RECIPE_SEMANTIC_VALIDATION_V2
def _recipe_result_refs(value):
    refs = []
    if isinstance(value, dict):
        if set(value) == {'$result'}:
            refs.append(str(value.get('$result') or ''))
        else:
            for item in value.values():
                refs.extend(_recipe_result_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_recipe_result_refs(item))
    return refs


def _validate_recipe_semantics(data):
    steps = data.get('steps', [])
    seen_results = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} must be an object')
        op = str(step.get('op') or '')
        if op.startswith('recipe.'):
            raise KnowledgeError(f'RECIPE_CYCLE_DETECTED: recipe step {index} recipe-to-recipe invocation is disabled: {op}')
        if op not in _RECIPE_ALLOWED_PRIMITIVES:
            raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} primitive not allowed: {op}')
        when = step.get('when')
        if when is not None:
            if not isinstance(when, dict):
                raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} when must be an object')
            if '$present' in when:
                if len(when) != 1 or not str(when.get('$present') or '').strip():
                    raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} invalid $present condition')
            elif '$result_equals' in when:
                spec = when.get('$result_equals')
                if not isinstance(spec, dict) or not str(spec.get('path') or '').strip():
                    raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} invalid $result_equals condition')
                root = str(spec['path']).split('.', 1)[0]
                if root not in seen_results:
                    raise KnowledgeError(
                        f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} condition references non-prior result: {spec["path"]}'
                    )
            else:
                raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} unsupported condition')
        for ref in _recipe_result_refs(step.get('arguments') or {}):
            root = str(ref).split('.', 1)[0]
            if root not in seen_results:
                raise KnowledgeError(
                    f'KNOWLEDGE_VALIDATION_FAILED: recipe step {index} argument references non-prior result: {ref}'
                )
        key = str(step.get('save_as') or f'step_{index}')
        if key in seen_results:
            raise KnowledgeError(f'KNOWLEDGE_VALIDATION_FAILED: duplicate recipe result key: {key}')
        seen_results.add(key)
    return data


# AI_BRIDGE_P0_ATOMIC_LAYER_FIX_V1
def _p0_validate_loaded(path, data):
    if path == ERROR_CATALOG or path == HOST_RULES:
        if not isinstance(data, dict) or not isinstance(data.get('rules', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: rules document must contain a list')
    if path == PROMOTION_REGISTRY:
        if not isinstance(data, dict) or not isinstance(data.get('entries', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: promotion registry must contain entries list')
    if path == TEMPLATE_CATALOG:
        if not isinstance(data, dict) or not isinstance(data.get('templates', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: template catalog must contain templates list')
    if path == ALIAS_CATALOG:
        if not isinstance(data, dict) or not isinstance(data.get('alias_sets', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: alias catalog must contain alias_sets list')
    try:
        is_recipe = path.parent == RECIPES_DIR and path.suffix.lower() == '.json'
    except Exception:
        is_recipe = False
    if is_recipe:
        if not isinstance(data, dict):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe must be an object')
        if not str(data.get('id') or '').strip():
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe id required')
        if not isinstance(data.get('steps', []), list):
            raise KnowledgeError('KNOWLEDGE_VALIDATION_FAILED: recipe steps must be a list')
        _validate_recipe_semantics(data)
    return data
