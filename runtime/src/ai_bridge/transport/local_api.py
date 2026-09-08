import hmac
from fastapi import Depends, FastAPI, Header, HTTPException, Response

from ai_bridge.core.service import BridgeService, DuplicateCommandError
from ai_bridge.core.sessions import SessionHeartbeat, SessionRegistration
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult


def create_app(service: BridgeService, *, auth_token: str) -> FastAPI:
    if not auth_token:
        raise ValueError("auth_token must not be empty")
    app = FastAPI(title="AI Bridge Local API")

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {auth_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")

    @app.get("/health", dependencies=[Depends(require_auth)])
    def health():
        return {
            "status": "ok",
            "write_blocked": service.emergency_stop.write_blocked,
            "sessions": [s.session_id for s in service.sessions.list()],
        }

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
    def adapter_result(session_id: str, result: ExecutionResult):
        try:
            service.sessions.touch(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        accepted = service.adapter_bus.complete(result)
        if not accepted:
            raise HTTPException(status_code=409, detail="COMMAND_NOT_PENDING")
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
