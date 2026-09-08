from __future__ import annotations

import ntpath
import os
import threading

from . import capability_guidance, checkpoint_ops, code_ops, compat_ops, cook_ops, error_ops, graph_ops, inspect_ops, knowledge_registry, node_ops, parm_ops
from . import local_log


_dispatch_context = threading.local()


def _base(command_id, session_id):
    return {
        "command_id": command_id,
        "status": "success",
        "stages": {},
        "result": {},
        "failure": None,
        "rollback_available": False,
        "last_known_state": {"host": "alive", "session": session_id},
        "evidence_id": None,
    }


def _result(command_id, session_id, payload, *, stages=None):
    out = _base(command_id, session_id)
    if isinstance(payload, dict) and payload.get("conflict"):
        out["status"] = "conflict"
        out["stages"] = {"WRITE": "NOT_RUN"}
        out["result"] = payload
        out["failure"] = {
            "origin": "host",
            "stage": "precondition",
            "code": "CONFLICT",
            "category": "conflict",
            "message": "The host state changed since it was last read.",
            "retryable": True,
            "suggestion": "Re-read the current state, reconcile the change, and retry with a fresh expected_hash.",
        }
        return out
    if isinstance(payload, dict) and payload.get("verified") is False:
        out["status"] = "failed"
        out["result"] = payload
        inner_failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else None
        if inner_failure and inner_failure.get("code"):
            out["stages"] = {"EXECUTE": "FAILED"}
            out["failure"] = {
                "origin": "host",
                "stage": "execute",
                "code": str(inner_failure.get("code")),
                "category": str(inner_failure.get("category") or "execution"),
                "message": str(inner_failure.get("message") or "The nested operation failed."),
                "retryable": bool(inner_failure.get("retryable", False)),
                "suggestion": inner_failure.get("suggestion"),
                "underlying": inner_failure,
            }
        else:
            out["stages"] = {"WRITE": "FAILED", "READBACK": "FAILED"}
            out["failure"] = {
                "origin": "host",
                "stage": "verify",
                "code": "READBACK_MISMATCH",
                "category": "verification",
                "message": "The readback result does not match the requested write.",
                "retryable": False,
                "suggestion": "Inspect the affected node/parameter before retrying.",
            }
        return out
    out["result"] = payload
    out["stages"] = stages or {"EXECUTE": "PASS", "READBACK": "VERIFIED"}
    operation = getattr(_dispatch_context, "operation", None)
    suggestions = capability_guidance.observe(session_id, operation) if operation else []
    if suggestions:
        out["guidance"] = {
            "mode": "advisory_only",
            "blocking": False,
            "auto_execute": False,
            "suggestions": suggestions,
        }
    return out


def _same_project(left, right):
    if not left or not right:
        return False
    left_text = str(left).strip()
    right_text = str(right).strip()
    windows_style = (
        (len(left_text) >= 2 and left_text[1] == ":")
        or (len(right_text) >= 2 and right_text[1] == ":")
        or "\\" in left_text
        or "\\" in right_text
    )
    path_module = ntpath if windows_style else os.path
    try:
        return path_module.normcase(path_module.abspath(left_text)) == path_module.normcase(
            path_module.abspath(right_text)
        )
    except Exception:
        left_fallback = left_text.replace("\\", "/")
        right_fallback = right_text.replace("\\", "/")
        if windows_style:
            left_fallback = left_fallback.casefold()
            right_fallback = right_fallback.casefold()
        return left_fallback == right_fallback


def _required_arg(args: dict, *names: str):
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    primary = names[0] if names else "argument"
    raise ValueError(f"ARGUMENT_REQUIRED: {primary}")


def dispatch(hou, command: dict, session_info: dict) -> dict:
    command_id = command["command_id"]
    session_id = session_info["session_id"]
    op = command["operation"]
    args = command.get("arguments") or {}
    _dispatch_context.operation = op
    _dispatch_context.session_id = session_id
    try:
        expected_project = command.get("project_file")
        if expected_project is not None:
            actual_project = hou.hipFile.path()
            if not _same_project(actual_project, expected_project):
                out = _base(command_id, session_id)
                out["status"] = "conflict"
                out["failure"] = {
                    "origin": "host",
                    "stage": "precondition",
                    "code": "PROJECT_FILE_MISMATCH",
                    "category": "wrong_target",
                    "message": f"Expected project {expected_project!r}, but Houdini has {actual_project!r} open.",
                    "retryable": True,
                    "suggestion": "Refresh Bridge status and target the currently connected project/session.",
                    "context": {
                        "expected_project_file": expected_project,
                        "actual_project_file": actual_project,
                        "operation": op,
                    },
                }
                out["last_known_state"]["project_file"] = actual_project
                return out

        if op == "session.status":
            return _result(command_id, session_id, dict(session_info), stages={"READBACK": "VERIFIED"})
        if op == "inspect.context":
            payload = inspect_ops.inspect_context(hou)
            payload["session_id"] = session_id
            return _result(command_id, session_id, payload, stages={"READBACK": "VERIFIED"})
        if op == "inspect.node":
            return _result(command_id, session_id, inspect_ops.inspect_node(hou, args["path"], args.get("mode", "normal")), stages={"READBACK": "VERIFIED"})
        if op == "inspect.parm_template":
            return _result(command_id, session_id, inspect_ops.inspect_parm_template(hou, args["path"], args["parameter"]), stages={"READBACK": "VERIFIED"})
        if op == "inspect.batch_nodes":
            return _result(
                command_id,
                session_id,
                inspect_ops.inspect_batch_nodes(
                    hou,
                    args.get("paths"),
                    mode=args.get("mode", args.get("detail_level", "summary")),
                    max_details=int(args.get("max_details", 200)),
                    fields=args.get("fields"),
                ),
                stages={"READBACK": "VERIFIED"},
            )
        if op == "inspect.network":
            return _result(command_id, session_id, inspect_ops.inspect_network(hou, args["path"], int(args.get("depth", 1)), args.get("mode", "summary"), int(args.get("max_nodes", 500))), stages={"READBACK": "VERIFIED"})
        if op == "inspect.find":
            return _result(command_id, session_id, inspect_ops.inspect_find(hou, args.get("root", "/obj"), node_type=args.get("node_type"), name_contains=args.get("name_contains"), max_results=int(args.get("max_results", 100))), stages={"READBACK": "VERIFIED"})

        if op == "parm.read":
            return _result(command_id, session_id, parm_ops.read(hou, args["path"], args["parameter"]), stages={"READBACK": "VERIFIED"})
        if op == "parm.write":
            return _result(command_id, session_id, parm_ops.write(hou, args["path"], args["parameter"], args["value"], args.get("expected_hash")))
        if op == "parm.batch_read":
            items, input_mode = compat_ops.normalize_parm_batch_read_args(args)
            payload = compat_ops.parm_batch_read(hou, items)
            payload["input_mode"] = input_mode
            return _result(command_id, session_id, payload, stages={"READBACK": "VERIFIED"})
        if op == "parm.batch_write":
            items, input_mode = compat_ops.normalize_parm_batch_write_args(args)
            payload = compat_ops.parm_batch_write(hou, items)
            payload["input_mode"] = input_mode
            return _result(command_id, session_id, payload, stages={"READBACK": "VERIFIED"})

        if op == "code.read":
            return _result(command_id, session_id, code_ops.read(hou, args["path"], args["parameter"]), stages={"READBACK": "VERIFIED"})
        if op == "code.write":
            return _result(command_id, session_id, code_ops.write(hou, args["path"], args["parameter"], args["text"], args.get("expected_hash")))
        if op == "code.patch":
            return _result(command_id, session_id, code_ops.patch(hou, args["path"], args["parameter"], args["old_text"], args["new_text"], args.get("expected_hash")))

        if op == "node.create":
            return _result(command_id, session_id, node_ops.create(hou, args["parent"], args["node_type"], args["name"]))
        if op == "node.create_configured":
            return _result(
                command_id,
                session_id,
                graph_ops.create_configured(
                    hou,
                    args["parent"],
                    args["node_type"],
                    args["name"],
                    args.get("parms"),
                    args.get("state"),
                    args.get("inputs"),
                ),
                stages={"EXECUTE": "PASS", "READBACK": "VERIFIED"},
            )
        if op == "node.delete":
            return _result(command_id, session_id, node_ops.delete(hou, args["path"], args["expected_type"], args["expected_name"]))
        if op == "node.connect":
            return _result(command_id, session_id, node_ops.connect(hou, args["target"], args["input_index"], args["source"], args.get("output_index", 0), args.get("expected_hash")))
        if op == "node.disconnect":
            return _result(command_id, session_id, node_ops.disconnect(hou, args["target"], args["input_index"], args.get("expected_hash")))
        if op == "node.input_state":
            return _result(command_id, session_id, node_ops.input_state(hou, args["target"], args["input_index"]), stages={"READBACK": "VERIFIED"})
        if op == "node.batch_connect":
            items, input_mode = node_ops.normalize_batch_connect_args(args)
            payload = node_ops.batch_connect(hou, items)
            payload["input_mode"] = input_mode
            return _result(command_id, session_id, payload)
        if op == "node.state":
            return _result(command_id, session_id, compat_ops.node_state(hou, args["path"]), stages={"READBACK": "VERIFIED"})
        if op == "node.set_state":
            return _result(command_id, session_id, compat_ops.node_set_state(hou, args["path"], args.get("values", {})), stages={"READBACK": "VERIFIED"})

        if op == "network.validate":
            return _result(
                command_id,
                session_id,
                graph_ops.validate_spec(hou, args["parent"], args.get("nodes", []), args.get("connections", [])),
                stages={"READBACK": "VERIFIED"},
            )
        if op == "network.apply":
            return _result(
                command_id,
                session_id,
                graph_ops.apply_spec(
                    hou,
                    args["parent"],
                    args.get("nodes", []),
                    args.get("connections", []),
                    layout=bool(args.get("layout", False)),
                    allow_update_existing=bool(args.get("allow_update_existing", False)),
                ),
                stages={"EXECUTE": "PASS", "READBACK": "VERIFIED"},
            )

        if op == "selection.get":
            return _result(command_id, session_id, compat_ops.selection_get(hou), stages={"READBACK": "VERIFIED"})
        if op == "selection.set":
            return _result(command_id, session_id, compat_ops.selection_set(hou, args.get("paths", []), args.get("current")), stages={"READBACK": "VERIFIED"})

        if op == "frame.set":
            return _result(command_id, session_id, cook_ops.set_frame(hou, args["frame"]))
        if op == "network.apply_transactional":
            return _result(
                command_id,
                session_id,
                graph_ops.apply_transactional(
                    hou,
                    args["parent"],
                    args.get("nodes", []),
                    args.get("connections", []),
                    layout=bool(args.get("layout", False)),
                ),
                stages={"EXECUTE": "PASS", "READBACK": "VERIFIED"},
            )

        if op == "network.ensure_plan":
            payload = graph_ops.plan_ensure(
                hou,
                args["parent"],
                args.get("nodes", []),
                args.get("connections", []),
                cook_path=args.get("cook_path"),
                max_details=int(args.get("max_details", 200)),
            )
            return _result(
                command_id,
                session_id,
                payload,
                stages={"READBACK": "VERIFIED"},
            )

        if op == "network.ensure_transactional":
            payload = graph_ops.ensure_transactional(
                hou,
                args["parent"],
                args.get("nodes", []),
                args.get("connections", []),
                layout=bool(args.get("layout", False)),
                cook_path=args.get("cook_path"),
                force=bool(args.get("force", True)),
                expected_plan_hash=args.get("expected_plan_hash"),
            )
            return _result(
                command_id,
                session_id,
                payload,
                stages={
                    "EXECUTE": "PASS" if payload.get("verified") else "FAILED",
                    "READBACK": "VERIFIED" if payload.get("verified") else "FAILED",
                    **({"COOK": payload["cook"]["cook_status"]} if payload.get("cook") else {}),
                },
            )

        if op == "cook.execute":
            payload = cook_ops.cook(hou, args["path"], args.get("force", True))
            out = _result(command_id, session_id, payload, stages={"COOK": payload["cook_status"]})
            if payload["cook_status"] == "FAILED":
                out.pop("guidance", None)
                out["status"] = "failed"
                out["failure"] = error_ops.cook_failure(payload, operation=op, arguments=args)
            return out
        if op == "host.errors":
            return _result(command_id, session_id, cook_ops.host_errors(hou, args["path"]), stages={"READBACK": "VERIFIED"})

        if op == "hip.status":
            return _result(command_id, session_id, compat_ops.hip_status(hou), stages={"READBACK": "VERIFIED"})
        if op == "hip.save":
            return _result(command_id, session_id, compat_ops.hip_save(hou, args.get("path")), stages={"READBACK": "VERIFIED"})

        if op == "checkpoint.create":
            return _result(command_id, session_id, checkpoint_ops.create(hou, args["checkpoint_id"]), stages={"CHECKPOINT": "VERIFIED"})
        if op == "rollback.execute":
            return _result(command_id, session_id, checkpoint_ops.rollback(hou, args["checkpoint_path"], args["original_hip"]), stages={"ROLLBACK": "VERIFIED"})

        if op == "knowledge.status":
            return _result(command_id, session_id, knowledge_registry.status(), stages={"READBACK": "VERIFIED"})
        if op == "knowledge.search":
            return _result(command_id, session_id, knowledge_registry.search(args["query"], int(args.get("limit", 20))), stages={"READBACK": "VERIFIED"})
        if op == "recipe.list":
            return _result(command_id, session_id, {"recipes": knowledge_registry.recipes()}, stages={"READBACK": "VERIFIED"})
        if op == "recipe.get":
            return _result(command_id, session_id, knowledge_registry.get_recipe(_required_arg(args, "recipe", "recipe_id")), stages={"READBACK": "VERIFIED"})
        if op == "recipe.validate":
            return _result(command_id, session_id, knowledge_registry.validate_recipe(_required_arg(args, "recipe", "recipe_id"), args.get("values", args.get("inputs", {})), hou=hou), stages={"READBACK": "VERIFIED"})
        if op == "recipe.apply_validation":
            recipe_id = _required_arg(args, "recipe", "recipe_id")
            payload = knowledge_registry.apply_recipe(
                hou,
                recipe_id,
                args.get("values", args.get("inputs", {})),
            )
            if isinstance(payload, dict):
                payload["validation_only"] = True
                payload["promotion_state"] = knowledge_registry.recipe_promotion_state(recipe_id)
            return _result(
                command_id,
                session_id,
                payload,
                stages={"EXECUTE": "PASS", "READBACK": "VERIFIED"},
            )
        if op == "recipe.apply":
            recipe_id = _required_arg(args, "recipe", "recipe_id")
            promotion_state = knowledge_registry.recipe_promotion_state(recipe_id)
            if promotion_state != "promoted":
                out = _base(command_id, session_id)
                out["status"] = "denied"
                out["stages"] = {"EXECUTE": "NOT_RUN"}
                out["failure"] = {
                    "origin": "adapter",
                    "stage": "precondition",
                    "code": "RECIPE_NOT_PROMOTED",
                    "category": "knowledge",
                    "message": f"Recipe is not execution-authorized: {recipe_id} state={promotion_state}",
                    "retryable": False,
                    "suggestion": "Use a promoted recipe or explicit typed primitives; candidate/unregistered recipes remain inspectable via recipe.get/recipe.validate.",
                }
                return out
            return _result(command_id, session_id, knowledge_registry.apply_recipe(hou, recipe_id, args.get("values", args.get("inputs", {}))), stages={"EXECUTE": "PASS", "READBACK": "VERIFIED"})

        if op == "capability.search":
            return _result(command_id, session_id, compat_ops.capability_search(hou, session_info, args.get("query"), int(args.get("limit", 20))), stages={"READBACK": "VERIFIED"})

        if op == "adapter.capabilities":
            return _result(command_id, session_id, compat_ops.adapter_capabilities(hou, session_info), stages={"READBACK": "VERIFIED"})

        out = _base(command_id, session_id)
        out["status"] = "denied"
        supported = [item.get("name") for item in session_info.get("capabilities", []) if isinstance(item, dict)]
        out["failure"] = error_ops.unsupported_capability(op, supported)
        return out
    except Exception as exc:
        out = _base(command_id, session_id)
        out["status"] = "failed"
        out["stages"] = {"EXECUTE": "FAILED"}
        out["failure"] = error_ops.classify_exception(exc, operation=op, arguments=args)
        return out
