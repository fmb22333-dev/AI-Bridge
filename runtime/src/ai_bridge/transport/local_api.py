import hmac
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from pydantic import ValidationError

from ai_bridge.adapters.scene_store import (
    ADAPTER_NAME as SCENE_STORE_ADAPTER,
    SceneStoreExecutor,
    descriptor as scene_store_descriptor,
)
from ai_bridge.core.service import BridgeService, DuplicateCommandError
from ai_bridge.core.sessions import SessionHeartbeat, SessionRegistration
from ai_bridge.persistence.scene_captures import SceneCaptureStore
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import (
    ExecutionResult,
    ExecutionStatus,
    FailureInfo,
    FailureOrigin,
)


def _install_scene_store(service: BridgeService):
    """Attach the local immutable scene store when the service exposes a DB root."""
    store = getattr(service, "scene_captures", None)
    if store is None:
        db = getattr(service, "db", None)
        db_path = getattr(db, "path", None)
        if db_path is not None:
            store = SceneCaptureStore(db_path.parent / "state" / "scene_captures")
            setattr(service, "scene_captures", store)
    if (
        store is not None
        and hasattr(service, "adapter_registry")
        and hasattr(service, "register_executor")
    ):
        service.adapter_registry.register(scene_store_descriptor(), replace=True)
        service.register_executor(SCENE_STORE_ADAPTER, SceneStoreExecutor(store=store))
    return store


def _normalize_adapter_result(payload: Any) -> tuple[ExecutionResult | None, dict | None]:
    try:
        return ExecutionResult.model_validate(payload), None
    except ValidationError as exc:
        raw = payload if isinstance(payload, dict) else {}
        command_id = str(raw.get("command_id") or "").strip()
        errors = exc.errors(include_url=False, include_input=False)[:8]
        if not command_id:
            return None, {
                "accepted": False, "discarded": True,
                "error": "ADAPTER_RESULT_SCHEMA_INVALID", "detail": errors,
            }
        return (
            ExecutionResult(
                command_id=command_id, status=ExecutionStatus.FAILED,
                failure=FailureInfo(
                    origin=FailureOrigin.ADAPTER, stage="result_ingress",
                    code="ADAPTER_RESULT_SCHEMA_INVALID",
                    message="Adapter result did not satisfy the Core result schema.",
                    category="adapter_contract", retryable=False,
                    suggestion="Update the Adapter/Core protocol contract before retrying the source command.",
                    context={"validation_errors": errors},
                ),
                last_known_state={"host": "degraded"},
            ),
            {"accepted": True, "normalized_invalid": True, "error": "ADAPTER_RESULT_SCHEMA_INVALID"},
        )


def _state_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _local_client_context(service: BridgeService) -> dict:
    sessions = []
    for session in service.active_sessions():
        try:
            state = _state_value(service.sessions.status(session.session_id).state)
        except Exception:
            state = "unknown"
        sessions.append({
            "session_id": session.session_id,
            "adapter": session.adapter,
            "adapter_version": session.adapter_version,
            "host_version": session.host_version,
            "project_file": session.project_file,
            "pid": session.pid,
            "state": state,
        })
    sessions.sort(key=lambda item: (str(item["adapter"]), str(item["session_id"])))
    workspaces = sorted(
        item.workspace_id for item in service.workspaces.list()
        if not str(item.workspace_id).startswith("__")
    )
    return {
        "protocol": "bridge-local-client/1",
        "transport": "localhost_http",
        "workspaces": workspaces,
        "sessions": sessions,
        "endpoints": {
            "context": "GET /client/context",
            "commands": "POST /client/commands",
            "results": "GET /results/{command_id}",
        },
        "accepted_command_shapes": ["bare_command_envelope", "v5_style_command_wrapper"],
        "targeting": {
            "implicit_session_selection": False,
            "implicit_project_selection": False,
            "rule": "Read this context and send explicit current session/project targeting for Host commands.",
        },
        "wrapper_semantics": {
            "transport_metadata_ignored": ["bridge_id", "channel_id", "generation"],
            "predecessor_supported": False,
        },
    }


def _local_command_payload(payload: Any) -> CommandEnvelope:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="LOCAL_COMMAND_BODY_MUST_BE_OBJECT")
    if "command" in payload:
        if payload.get("predecessor") is not None:
            raise HTTPException(status_code=400, detail="LOCAL_PREDECESSOR_UNSUPPORTED")
        raw_command = payload.get("command")
    else:
        raw_command = payload
    try:
        return CommandEnvelope.model_validate(raw_command)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_input=False)[:8]) from exc


def create_app(service: BridgeService, *, auth_token: str, lifespan=None) -> FastAPI:
    if not auth_token:
        raise ValueError("auth_token must not be empty")
    app = FastAPI(title="AI Bridge Local API", lifespan=lifespan)
    scene_captures = _install_scene_store(service)

    if hasattr(service.adapter_bus, "set_timeout_handler"):
        service.adapter_bus.set_timeout_handler(
            lambda session_id, command: service.sessions.mark_busy_unknown(
                session_id, command_id=command.command_id, operation=command.operation
            )
        )
    if hasattr(service.adapter_bus, "set_late_completion_handler"):
        service.adapter_bus.set_late_completion_handler(
            lambda session_id, command_id, result: service.sessions.clear_busy_unknown(
                session_id, command_id=command_id
            )
        )

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {auth_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")

    @app.get("/health", dependencies=[Depends(require_auth)])
    def health():
        runtime_state = getattr(app.state, "runtime_state", None)
        remote = {}
        if isinstance(runtime_state, dict):
            raw_remote = runtime_state.get("remote")
            if isinstance(raw_remote, dict):
                remote = dict(raw_remote)
        return {
            "status": "ok",
            "write_blocked": service.emergency_stop.write_blocked,
            "sessions": [s.session_id for s in service.sessions.list()],
            "remote": remote,
        }

    @app.get("/client/context", dependencies=[Depends(require_auth)])
    def client_context():
        return _local_client_context(service)

    @app.post("/client/commands", dependencies=[Depends(require_auth)])
    def client_commands(payload: Any = Body(...)):
        command = _local_command_payload(payload)
        try:
            return service.execute(command)
        except DuplicateCommandError:
            raise HTTPException(status_code=409, detail="COMMAND_ID_ALREADY_EXISTS")

    @app.post("/commands", dependencies=[Depends(require_auth)])
    def commands(command: CommandEnvelope):
        try:
            return service.execute(command)
        except DuplicateCommandError:
            raise HTTPException(status_code=409, detail="COMMAND_ID_ALREADY_EXISTS")

    @app.get("/results/{command_id}", dependencies=[Depends(require_auth)])
    def result(command_id: str):
        row = service.db.get_command(command_id)
        if row is None:
            raise HTTPException(status_code=404, detail="COMMAND_NOT_FOUND")
        return row

    @app.post("/adapter/register", dependencies=[Depends(require_auth)])
    def register_adapter(registration: SessionRegistration):
        try:
            service.register_adapter_session(registration)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"status": "registered", "session_id": registration.session_id}

    @app.post("/adapter/heartbeat/{session_id}", dependencies=[Depends(require_auth)])
    def adapter_heartbeat(session_id: str, heartbeat: SessionHeartbeat):
        try:
            info = service.sessions.heartbeat(session_id, project_file=heartbeat.project_file)
        except KeyError:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        return {"session_id": info.session_id, "project_file": info.project_file, "last_seen_at": info.last_seen_at}

    @app.get("/adapter/poll/{session_id}", dependencies=[Depends(require_auth)])
    def adapter_poll(session_id: str, timeout: float = 15.0):
        try:
            service.sessions.touch(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        timeout = max(0.0, min(float(timeout), 30.0))
        command = service.adapter_bus.poll(session_id, timeout=timeout)
        if command is None:
            return Response(status_code=204)
        return command.model_dump(mode="json")

    @app.post("/adapter/result/{session_id}", dependencies=[Depends(require_auth)])
    def adapter_result(session_id: str, result_payload: Any = Body(...)):
        try:
            session = service.sessions.touch(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        result, invalid_response = _normalize_adapter_result(result_payload)
        if result is None:
            return invalid_response
        if (
            getattr(session, "adapter", None) == "unreal"
            and result.status == ExecutionStatus.SUCCESS
            and scene_captures is not None
            and scene_captures.is_supported_scene(result.result)
        ):
            try:
                result.result = scene_captures.capture(
                    command_id=result.command_id, session_id=session_id, scene=result.result,
                )
            except Exception as exc:
                bridge_meta = result.result.setdefault("_bridge", {})
                bridge_meta["scene_capture"] = {
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                    "raw_result_preserved": True,
                }
        accepted = service.adapter_bus.complete(result)
        if not accepted:
            raise HTTPException(status_code=409, detail="COMMAND_NOT_PENDING")
        if invalid_response is not None:
            return invalid_response
        return {"accepted": True}

    @app.post("/rollback/{command_id}", dependencies=[Depends(require_auth)])
    def rollback(command_id: str):
        try:
            return service.rollback_command(command_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="SNAPSHOT_NOT_FOUND")

    @app.post("/emergency-stop", dependencies=[Depends(require_auth)])
    def emergency_stop():
        canceled = service.stop_writes()
        return {"write_blocked": True, "queued_writes_canceled": canceled}

    @app.post("/emergency-resume", dependencies=[Depends(require_auth)])
    def emergency_resume():
        service.resume_writes()
        return {"write_blocked": False}

    return app
