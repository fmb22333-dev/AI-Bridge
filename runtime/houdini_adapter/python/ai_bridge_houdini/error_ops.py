from __future__ import annotations

from typing import Any


def _message(exc: BaseException) -> str:
    try:
        return str(exc) or type(exc).__name__
    except Exception:
        return type(exc).__name__


def _knowledge_match(message: str) -> dict | None:
    try:
        from . import knowledge_registry
        return knowledge_registry.match_error_text(message)
    except Exception:
        return None


def _from_knowledge(rule: dict, *, message: str, operation: str | None, arguments: dict | None, exc_type: str | None = None) -> dict:
    args=dict(arguments or {})
    context={"operation":operation}
    for key in ("path","parent","parameter","node_type","name","source","target","input_index","output_index"):
        if key in args:
            context[key]=args[key]
    return {
        "origin":"host",
        "stage":"execute",
        "code":str(rule.get("code") or "HOST_ERROR"),
        "category":str(rule.get("category") or "knowledge_rule"),
        "exception_type":exc_type,
        "message":message,
        "retryable":bool(rule.get("retryable",False)),
        "suggestion":rule.get("suggestion"),
        "knowledge_rule":rule.get("id"),
        "context":context,
    }


def classify_exception(exc: BaseException, *, operation: str | None = None, arguments: dict | None = None) -> dict:
    message=_message(exc)
    learned=_knowledge_match(message)
    if learned is not None:
        return _from_knowledge(learned,message=message,operation=operation,arguments=arguments,exc_type=type(exc).__name__)

    upper=message.upper()
    exc_type=type(exc).__name__
    args=dict(arguments or {})
    code=exc_type.upper()
    category="host_error"
    retryable=False
    suggestion=None

    rules=[
        ("ARGUMENT_REQUIRED","ARGUMENT_REQUIRED","invalid_argument","Provide the required operation argument and retry."),
        ("EXPECTED_HASH_REQUIRED","EXPECTED_HASH_REQUIRED","precondition","Read the current value first and retry with expected_hash."),
        ("HASH_MISMATCH","CONFLICT","conflict","Re-read current state and retry with a fresh expected_hash."),
        ("NODE_TYPE_NOT_FOUND","NODE_TYPE_NOT_FOUND","invalid_type","Run network.validate before applying the graph."),
        ("NODE TYPE NOT FOUND","NODE_TYPE_NOT_FOUND","invalid_type","Run network.validate before applying the graph."),
        ("NODE NOT FOUND","NODE_NOT_FOUND","missing_resource","Inspect the network or use inspect.find to resolve the current path."),
        ("NODE REFERENCE NOT FOUND","NODE_NOT_FOUND","missing_resource","Inspect the network or use inspect.find to resolve the current path."),
        ("PARAMETER NOT FOUND","PARM_NOT_FOUND","invalid_parameter","Inspect the node or run network.validate before writing parameters."),
        ("PARM_NOT_FOUND","PARM_NOT_FOUND","invalid_parameter","Inspect the node or run network.validate before writing parameters."),
        ("NODE ALREADY EXISTS","NODE_ALREADY_EXISTS","conflict","Use a unique name or reference the existing node explicitly."),
        ("DUPLICATE_NODE_NAME","DUPLICATE_NODE_NAME","invalid_argument","Use unique node names/ids in the graph specification."),
        ("NODE_TYPE_MISMATCH","NODE_TYPE_MISMATCH","conflict","Inspect the existing node type before updating it."),
        ("SOURCE_NOT_FOUND","CONNECTION_ENDPOINT_NOT_FOUND","missing_resource","Validate all connection endpoints before applying the graph."),
        ("TARGET_NOT_FOUND","CONNECTION_ENDPOINT_NOT_FOUND","missing_resource","Validate all connection endpoints before applying the graph."),
        ("INVALID_CONNECTION_INDEX","INVALID_CONNECTION_INDEX","invalid_argument","Use non-negative connection indices supported by the target node."),
    ]
    for token,new_code,new_category,new_suggestion in rules:
        if token in upper:
            code=new_code
            category=new_category
            suggestion=new_suggestion
            break
    else:
        if exc_type in {"PermissionError","OperationPermissionError"} or "PERMISSION" in upper:
            code="PERMISSION_DENIED"
            category="permission"
            suggestion="Check node lock state, asset permissions, and whether the context is editable."
        elif exc_type in {"ObjectWasDeleted","ObjectWasDeletedError"}:
            code="OBJECT_WAS_DELETED"
            category="stale_reference"
            retryable=True
            suggestion="Re-inspect the network and resolve a fresh node reference."
        elif exc_type in {"OperationFailed","OperationFailedError"}:
            code="HOUDINI_OPERATION_FAILED"
            category="houdini_operation"
            suggestion="Inspect host.errors and the affected node for Houdini-side diagnostics."

    context:dict[str,Any]={"operation":operation}
    for key in ("path","parent","parameter","node_type","name","source","target","input_index","output_index"):
        if key in args:
            context[key]=args[key]

    return {
        "origin":"host",
        "stage":"execute",
        "code":code,
        "category":category,
        "exception_type":exc_type,
        "message":message,
        "retryable":retryable,
        "suggestion":suggestion,
        "context":context,
    }


def cook_failure(payload: dict, *, operation: str = "cook.execute", arguments: dict | None = None) -> dict:
    errors=list(payload.get("errors") or [])
    warnings=list(payload.get("warnings") or [])
    args=dict(arguments or {})
    message=errors[0] if errors else "Houdini cook failed."
    learned=_knowledge_match(message)
    if learned is not None:
        out=_from_knowledge(learned,message=message,operation=operation,arguments=args,exc_type=None)
        out["stage"]="cook"
        out["host_errors"]=errors
        out["host_warnings"]=warnings
        return out
    return {
        "origin":"host",
        "stage":"cook",
        "code":"HOST_COOK_ERROR",
        "category":"cook_error",
        "exception_type":None,
        "message":message,
        "retryable":False,
        "suggestion":"Inspect host.errors on the affected node and fix the first host-side error.",
        "context":{"operation":operation,"path":args.get("path"),"force":args.get("force")},
        "host_errors":errors,
        "host_warnings":warnings,
    }


def unsupported_capability(operation: str, supported: list[str] | None = None) -> dict:
    result={
        "origin":"adapter",
        "stage":"dispatch",
        "code":"CAPABILITY_NOT_SUPPORTED",
        "category":"unsupported",
        "exception_type":None,
        "message":f"Unsupported Houdini adapter operation: {operation}",
        "retryable":False,
        "suggestion":"Call adapter.capabilities and choose a supported typed operation.",
        "context":{"operation":operation},
    }
    if supported is not None:
        result["supported_operations"]=supported
    return result
