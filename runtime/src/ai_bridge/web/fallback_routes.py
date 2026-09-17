from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ai_bridge.transport.remote_controller import RemoteConfigurationError
from ai_bridge.transport.supabase_primary_extension import install_supabase_primary_extension


# Install before the RemoteController instance is constructed by app.py.
install_supabase_primary_extension()


class SupabaseFallbackRequest(BaseModel):
    """Compatibility request name retained through Runtime 0.2.6.x."""

    project_url: str = Field(min_length=8)
    secret_key: str = ""
    poll_interval_seconds: float = Field(default=0.5, ge=0.25, le=60.0)
    table: str = Field(default="ai_bridge_commands", min_length=1, max_length=128)


def install_fallback_routes(
    app: FastAPI,
    *,
    auth_token: str,
    web_root: Path,
) -> None:
    """Install Supabase Primary UI/API while preserving fallback-named routes.

    Existing `/control/fallback/*` and `supabaseBackupCard` names remain stable
    compatibility surfaces. Runtime semantics are Supabase primary command bus,
    GitHub V5 fallback command transport + durable authority.
    """

    web_root = Path(web_root)

    def controller():
        value = getattr(app.state, "remote_controller", None)
        if value is None:
            raise HTTPException(status_code=503, detail="REMOTE_CONTROLLER_UNAVAILABLE")
        return value

    def check_cookie(ai_bridge_token: str | None = Cookie(default=None)) -> None:
        if ai_bridge_token is None or not hmac.compare_digest(ai_bridge_token, auth_token):
            raise HTTPException(status_code=401, detail="CONTROL_AUTH_REQUIRED")

    @app.get("/setup", include_in_schema=False)
    def setup_page_with_fallback():
        html = (web_root / "templates" / "setup.html").read_text(encoding="utf-8")
        script = '<script src="/setup/fallback.js?v=0.2.6.56"></script>'
        if script not in html:
            html = html.replace("</body>", script + "\n</body>")
        response = HTMLResponse(html)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/setup/fallback.js", include_in_schema=False)
    def setup_fallback_js():
        text = (web_root / "static" / "fallback_setup.js").read_text(encoding="utf-8")
        response = Response(text, media_type="application/javascript")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/setup/supabase/state", include_in_schema=False)
    def setup_supabase_state():
        remote = getattr(app.state.remote_controller, "runtime_state", {}).get("remote", {}) if getattr(app.state, "remote_controller", None) is not None else {}
        primary = controller().supabase_state()
        return {
            "primary_configured": bool(remote.get("configured")),
            "primary_bridge_id": remote.get("bridge_id"),
            "fallback": primary,  # compatibility response key through 0.2.6.x
            "supabase": primary,
        }

    @app.post("/setup/supabase", include_in_schema=False)
    def setup_supabase(body: SupabaseFallbackRequest):
        try:
            result = controller().configure_supabase(
                project_url=body.project_url,
                secret_key=body.secret_key,
                poll_interval_seconds=body.poll_interval_seconds,
                table=body.table,
            )
            return {"ok": True, "fallback": result, "supabase": result}
        except RemoteConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/control/app.js", include_in_schema=False)
    def dashboard_js(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        base = (web_root / "static" / "app.js").read_text(encoding="utf-8")
        primary = (web_root / "static" / "fallback_dashboard.js").read_text(encoding="utf-8")
        response = Response(base + "\n\n" + primary, media_type="application/javascript")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/control/fallback/state", include_in_schema=False)
    def control_fallback_state(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return controller().supabase_state()

    @app.post("/control/fallback/test", include_in_schema=False)
    def control_fallback_test(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            return controller().test_supabase()
        except RemoteConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/control/fallback", include_in_schema=False)
    def control_fallback_disconnect(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return {"ok": True, "fallback": controller().disconnect_supabase()}
