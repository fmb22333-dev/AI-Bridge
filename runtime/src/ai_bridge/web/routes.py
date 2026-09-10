from __future__ import annotations

import hmac
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ai_bridge.config import save_workspaces
from ai_bridge.core.service import BridgeService
from ai_bridge.deployment.github_provision import (
    GitHubBusProvisionRequest,
    GitHubBusProvisioner,
    GitHubProvisionError,
)
from ai_bridge.transport.remote_config import (
    GitHubRemoteConfig,
    default_bridge_id,
    github_login_url,
    github_repository_new_url,
    github_repository_url,
    github_token_settings_url,
    github_token_template_url,
    normalize_github_repository,
)
from ai_bridge.transport.remote_controller import RemoteConfigurationError


class WorkspaceRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    root: str = Field(min_length=1)


class FolderPathResponse(BaseModel):
    path: str


class ExecutionBudgetRequest(BaseModel):
    budget_seconds: float = Field(ge=1.0, le=7200.0)
    auto_recover: bool | None = None


class ProjectExecutionBudgetRequest(ExecutionBudgetRequest):
    project_file: str = Field(min_length=1)


class WorkspaceExecutionBudgetRequest(ExecutionBudgetRequest):
    workspace_id: str = Field(min_length=1)


class PluginRestartRequest(BaseModel):
    session_id: str = Field(min_length=1)
    workspace_id: str | None = None
    force_restart: bool = False


class GitHubRemoteRequest(BaseModel):
    repository: str = Field(min_length=3)
    branch: str = Field(default="main", min_length=1)
    bridge_id: str = Field(min_length=1, max_length=96)
    token: str = ""


class GitHubProvisionBody(BaseModel):
    repository: str = Field(min_length=1)
    bridge_id: str = Field(min_length=1, max_length=96)
    runtime_source_repository: str = Field(default="fmb22333-dev/AI-Bridge", min_length=3)
    token: str = ""
    private: bool = True


def _local_github_token() -> str | None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "AI_BRIDGE_GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _local_github_login() -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None




def _default_houdini_user_dir() -> Path:
    override = os.environ.get("AI_BRIDGE_HOUDINI_USER_DIR")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    candidates = [home / "Documents" / "houdini21.0"]
    for onedrive in sorted(home.glob("OneDrive*")):
        candidates.extend([onedrive / "Documents" / "houdini21.0", onedrive / "文档" / "houdini21.0"])
    candidates.append(home / "houdini21.0")
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _open_local_path(path: Path) -> None:
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise RuntimeError(f"Unable to open path: {exc}") from exc


def _pick_folder_windows() -> str:
    if os.name != "nt":
        raise RuntimeError("Native folder picker is currently supported on Windows only")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("PowerShell was not found")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description='选择 AI Bridge Workspace 根目录'; "
        "$d.ShowNewFolderButton=$true; "
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Write-Output $d.SelectedPath}"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Folder picker failed").strip()
        raise RuntimeError(detail)
    return result.stdout.strip()


def _start_gh_web_login() -> None:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub CLI is not installed")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([gh, "auth", "login", "--web", "--hostname", "github.com"], **kwargs)


def install_control_routes(
    app: FastAPI,
    service: BridgeService,
    *,
    auth_token: str,
    web_root: Path,
    workspaces_file: Path,
    runtime_state: dict | None = None,
) -> None:
    runtime_state = runtime_state if runtime_state is not None else {}

    def check_cookie(ai_bridge_token: str | None = Cookie(default=None)) -> None:
        if ai_bridge_token is None or not hmac.compare_digest(ai_bridge_token, auth_token):
            raise HTTPException(status_code=401, detail="CONTROL_AUTH_REQUIRED")

    def controller():
        value = getattr(app.state, "remote_controller", None)
        if value is None:
            raise HTTPException(status_code=503, detail="REMOTE_CONTROLLER_UNAVAILABLE")
        return value

    def no_store_file(path: Path, *, media_type: str | None = None):
        response = FileResponse(path, media_type=media_type)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/control/bootstrap", include_in_schema=False)
    def bootstrap(token: str = Query(...), target: str = Query(default="/")):
        if not hmac.compare_digest(token, auth_token):
            raise HTTPException(status_code=401, detail="INVALID_CONTROL_TOKEN")
        if target not in {"/", "/setup"}:
            target = "/"
        response = RedirectResponse(url=target, status_code=303)
        response.set_cookie("ai_bridge_token", auth_token, httponly=True, samesite="strict")
        return response

    @app.get("/setup", include_in_schema=False)
    def setup_page():
        return FileResponse(web_root / "templates" / "setup.html")

    def runtime_source_repository() -> str:
        override = str(os.environ.get("AI_BRIDGE_RUNTIME_SOURCE_REPOSITORY") or "").strip()
        if override:
            return normalize_github_repository(override)
        source_file = workspaces_file.parent / "update_source.json"
        try:
            source_data = json.loads(source_file.read_text(encoding="utf-8"))
            configured = normalize_github_repository(str(source_data.get("repository") or ""))
            if configured:
                return configured
        except Exception:
            pass
        return "fmb22333-dev/AI-Bridge"

    @app.get("/setup/state", include_in_schema=False)
    def setup_state():
        remote = dict(runtime_state.get("remote") or {})
        return {
            "configured": bool(remote.get("configured")),
            "status": remote.get("status", "unconfigured"),
            "repository": remote.get("repository", ""),
            "branch": remote.get("branch", "main"),
            "bridge_id": remote.get("bridge_id") or remote.get("suggested_bridge_id") or default_bridge_id(),
            "credential_saved": bool(remote.get("credential_saved")),
            "gh_cli_available": shutil.which("gh") is not None,
            "gh_cli_authenticated": _local_github_token() is not None,
            "gh_login": _local_github_login() or "",
            "runtime_source_repository": runtime_source_repository(),
            "clean_provision_supported": True,
        }

    @app.post("/setup/provision", include_in_schema=False)
    def setup_provision(body: GitHubProvisionBody):
        token = body.token.strip() or _local_github_token()
        if not token:
            raise HTTPException(
                status_code=400,
                detail="No GitHub credential found. Paste a token, or sign in with GitHub CLI first.",
            )
        runtime_source = normalize_github_repository(body.runtime_source_repository)
        if runtime_source.count("/") != 1:
            raise HTTPException(status_code=400, detail="Runtime source must be owner/repository")
        try:
            provisioner = GitHubBusProvisioner(token)
            provisioned = provisioner.provision(
                GitHubBusProvisionRequest(
                    repository=body.repository,
                    bridge_id=body.bridge_id.strip(),
                    runtime_source_repository=runtime_source,
                    private=bool(body.private),
                )
            )
            remote = controller().configure_github(
                GitHubRemoteConfig(
                    repository=provisioned["repository"],
                    branch=provisioned["branch"],
                    bridge_id=body.bridge_id.strip(),
                ),
                token,
            )
            update_source = {
                "repository": runtime_source,
                "branch": "main",
                "manifest_path": "runtime-release.json",
                "bootstrap_from_bus": False,
            }
            (workspaces_file.parent / "update_source.json").write_text(
                json.dumps(update_source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            response = JSONResponse({
                "ok": True,
                "provisioned": provisioned,
                "remote": remote,
                "runtime_source": update_source,
            })
            response.set_cookie("ai_bridge_token", auth_token, httponly=True, samesite="strict")
            return response
        except (GitHubProvisionError, RemoteConfigurationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/setup/save", include_in_schema=False)
    def setup_save(body: GitHubRemoteRequest):
        token = body.token.strip() or _local_github_token()
        if not token:
            raise HTTPException(
                status_code=400,
                detail="No GitHub credential found. Paste a token, or sign in with GitHub CLI first.",
            )
        try:
            result = controller().configure_github(
                GitHubRemoteConfig(
                    repository=normalize_github_repository(body.repository),
                    branch=body.branch.strip(),
                    bridge_id=body.bridge_id.strip(),
                ),
                token,
            )
            response = JSONResponse({"ok": True, "remote": result})
            response.set_cookie("ai_bridge_token", auth_token, httponly=True, samesite="strict")
            return response
        except (RemoteConfigurationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/setup/github-login", include_in_schema=False)
    def setup_github_login():
        return RedirectResponse(url=github_login_url())

    @app.get("/setup/github-cli", include_in_schema=False)
    def setup_github_cli():
        return RedirectResponse(url="https://cli.github.com/")

    @app.get("/setup/github-token", include_in_schema=False)
    def setup_github_token(owner: str = Query(default="")):
        return RedirectResponse(url=github_token_template_url(owner))

    @app.get("/setup/github-token-settings", include_in_schema=False)
    def setup_github_token_settings():
        return RedirectResponse(url=github_token_settings_url())

    @app.get("/setup/github-repository", include_in_schema=False)
    def setup_github_repository():
        return RedirectResponse(url=github_repository_new_url())

    @app.get("/setup/github-open-repository", include_in_schema=False)
    def setup_github_open_repository(repository: str = Query(default="")):
        return RedirectResponse(url=github_repository_url(repository))

    @app.post("/setup/github-cli-login", include_in_schema=False)
    def setup_github_cli_login():
        try:
            _start_gh_web_login()
            return {"ok": True, "detail": "GitHub CLI login started in a separate window"}
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/", include_in_schema=False)
    def page(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return no_store_file(web_root / "templates" / "index.html")

    @app.get("/commands", include_in_schema=False)
    def commands_page(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return no_store_file(web_root / "templates" / "commands.html")

    @app.get("/control/app.js", include_in_schema=False)
    def js(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return no_store_file(web_root / "static" / "app.js", media_type="application/javascript")

    @app.get("/control/app.css", include_in_schema=False)
    def css(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return no_store_file(web_root / "static" / "app.css", media_type="text/css")

    @app.get("/control/commands.js", include_in_schema=False)
    def commands_js(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return no_store_file(web_root / "static" / "commands.js", media_type="application/javascript")

    @app.get("/control/status", include_in_schema=False)
    def status(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        active_projects = sorted({
            session.project_file
            for session in service.active_sessions()
            if session.project_file
        })
        return {
            "core": "ok",
            "write_blocked": service.emergency_stop.write_blocked,
            "execution_policy": service.execution_policy.status(
                active_projects=active_projects,
                active_workspaces=[
                    workspace.workspace_id
                    for workspace in service.workspaces.list()
                    if not workspace.workspace_id.startswith("__")
                ],
            ),
            "workspaces": [
                {"id": workspace.workspace_id, "root": str(workspace.root)}
                for workspace in service.workspaces.list()
                if not workspace.workspace_id.startswith("__")
            ],
            "sessions": [
                {
                    "session_id": session.session_id,
                    "adapter": session.adapter,
                    "adapter_version": session.adapter_version,
                    "host_version": session.host_version,
                    "project_file": session.project_file,
                    "pid": session.pid,
                    "state": session_status.state,
                    "last_seen_at": session_status.last_seen_at,
                    "seconds_since_seen": round(session_status.seconds_since_seen, 1),
                }
                for session in service.sessions.list()
                for session_status in [service.sessions.status(session.session_id)]
            ],
            "plugins": service.plugins.status(
                live_sessions=service.active_sessions()
            ),
            "commands": service.db.list_commands(5),
            "recoveries": [
                {
                    "recovery_id": item.get("recovery_id"),
                    "recorded_at": item.get("recorded_at"),
                    "status": item.get("status"),
                    "source_command_id": item.get("source_command_id"),
                    "source_operation": item.get("source_operation"),
                    "checkpoint_id": item.get("checkpoint_id"),
                    "reason": item.get("reason"),
                    "new_session_id": item.get("new_session_id"),
                }
                for item in service.recoveries.recent(5)
            ],
            "remote": runtime_state.get("remote", {"configured": False, "status": "unconfigured"}),
        }


    @app.get("/control/commands", include_in_schema=False)
    def command_history(
        limit: int = Query(default=100, ge=1, le=200),
        ai_bridge_token: str | None = Cookie(default=None),
    ):
        check_cookie(ai_bridge_token)
        return {"commands": service.db.list_commands(limit), "limit": limit}

    @app.get("/control/commands/{command_id}", include_in_schema=False)
    def command_detail(command_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        item = service.db.get_command(command_id)
        if item is None:
            raise HTTPException(status_code=404, detail="COMMAND_NOT_FOUND")
        return JSONResponse(item)

    @app.get("/control/supervisor/status", include_in_schema=False)
    def supervisor_status(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        path = os.environ.get("AI_BRIDGE_SUPERVISOR_STATUS")
        if not path:
            path = str(workspaces_file.parent / "supervisor_status.json")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"state": "unknown"}
        except Exception:
            return {"state": "unavailable"}

    @app.post("/control/workspaces", include_in_schema=False)
    def add_workspace(body: WorkspaceRequest, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            workspace = service.workspaces.register(body.workspace_id, Path(body.root))
        except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save_workspaces(workspaces_file, service.workspaces.list())
        return {"workspace_id": workspace.workspace_id, "root": str(workspace.root)}

    @app.delete("/control/workspaces/{workspace_id}", include_in_schema=False)
    def delete_workspace(workspace_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            workspace = service.workspaces.remove(workspace_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        save_workspaces(workspaces_file, service.workspaces.list())
        return {"removed": workspace.workspace_id, "root_untouched": str(workspace.root)}

    @app.get("/control/execution-policy", include_in_schema=False)
    def execution_policy(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        active_projects = sorted({
            session.project_file
            for session in service.active_sessions()
            if session.project_file
        })
        return service.execution_policy.status(
            active_projects=active_projects,
            active_workspaces=[
                workspace.workspace_id
                for workspace in service.workspaces.list()
                if not workspace.workspace_id.startswith("__")
            ],
        )

    @app.put("/control/execution-policy/default", include_in_schema=False)
    def set_default_execution_budget(body: ExecutionBudgetRequest, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            return service.execution_policy.set_default(
                body.budget_seconds,
                auto_recover=body.auto_recover,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.put("/control/execution-policy/workspace", include_in_schema=False)
    def set_workspace_execution_budget(body: WorkspaceExecutionBudgetRequest, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            service.workspaces.get(body.workspace_id)
            return service.execution_policy.set_workspace(
                body.workspace_id,
                body.budget_seconds,
                auto_recover=body.auto_recover,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="WORKSPACE_NOT_FOUND")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/control/execution-policy/workspace", include_in_schema=False)
    def clear_workspace_execution_budget(workspace_id: str = Query(...), ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return {
            "workspace_id": workspace_id,
            "removed": service.execution_policy.remove_workspace(workspace_id),
        }

    @app.put("/control/execution-policy/project", include_in_schema=False)
    def set_project_execution_budget(body: ProjectExecutionBudgetRequest, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            return service.execution_policy.set_project(body.project_file, body.budget_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/control/execution-policy/project", include_in_schema=False)
    def clear_project_execution_budget(project_file: str = Query(...), ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return {
            "project_file": project_file,
            "removed": service.execution_policy.remove_project(project_file),
        }

    @app.get("/control/plugins", include_in_schema=False)
    def plugin_status(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return service.plugins.status(live_sessions=service.active_sessions())

    @app.post("/control/plugins/install-all", include_in_schema=False)
    def plugin_install_all(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return service.plugins.install_all(live_sessions=service.active_sessions())

    @app.post("/control/plugins/{host_id}/install", include_in_schema=False)
    def plugin_install(host_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        host_id = host_id.strip().lower()
        host_running = any(
            session.adapter == host_id and service.sessions.is_active(session.session_id)
            for session in service.sessions.list(adapter=host_id)
        )
        try:
            result = service.plugins.install(host_id, host_running=host_running)
        except KeyError:
            raise HTTPException(status_code=404, detail="PLUGIN_HOST_NOT_FOUND")
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "install": result,
            "plugins": service.plugins.status(live_sessions=service.active_sessions()),
        }

    @app.post("/control/plugins/{host_id}/restart-apply", include_in_schema=False)
    def plugin_restart_apply(
        host_id: str,
        body: PluginRestartRequest,
        ai_bridge_token: str | None = Cookie(default=None),
    ):
        check_cookie(ai_bridge_token)
        user_workspaces = [
            item.workspace_id
            for item in service.workspaces.list()
            if not item.workspace_id.startswith("__")
        ]
        workspace = body.workspace_id
        if workspace is None:
            if "Bridge" in user_workspaces:
                workspace = "Bridge"
            elif user_workspaces:
                workspace = user_workspaces[0]
            else:
                raise HTTPException(status_code=400, detail="NO_USER_WORKSPACE_AVAILABLE")
        try:
            return service.restart_host_for_plugin_update(
                host_id=host_id,
                session_id=body.session_id,
                workspace=workspace,
                force_restart=body.force_restart,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/control/emergency-stop", include_in_schema=False)
    def stop(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return {"write_blocked": True, "queued_writes_canceled": service.stop_writes()}

    @app.post("/control/emergency-resume", include_in_schema=False)
    def resume(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        service.resume_writes()
        return {"write_blocked": False}

    @app.get("/control/remote/github/token-template", include_in_schema=False)
    def token_template(owner: str = Query(default=""), ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        return {"url": github_token_template_url(owner)}

    @app.post("/control/remote/github", include_in_schema=False)
    def github(body: GitHubRemoteRequest, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        token = body.token.strip() or _local_github_token()
        if not token:
            raise HTTPException(status_code=400, detail="GitHub token is required")
        try:
            return controller().configure_github(
                GitHubRemoteConfig(
                    repository=normalize_github_repository(body.repository),
                    branch=body.branch.strip(),
                    bridge_id=body.bridge_id.strip(),
                ),
                token,
            )
        except (RemoteConfigurationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/control/remote", include_in_schema=False)
    def disconnect(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        controller().disconnect()
        return {"configured": False, "status": "disabled"}

    @app.post("/control/system/pick-folder", include_in_schema=False)
    def pick_folder(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            path = _pick_folder_windows()
            return {"path": path, "canceled": not bool(path)}
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/control/system/open-data-dir", include_in_schema=False)
    def open_data_dir(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        path = workspaces_file.parent
        path.mkdir(parents=True, exist_ok=True)
        _open_local_path(path)
        return {"ok": True, "path": str(path)}

    @app.post("/control/system/open-houdini-dir", include_in_schema=False)
    def open_houdini_dir(ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        path = _default_houdini_user_dir()
        path.mkdir(parents=True, exist_ok=True)
        _open_local_path(path)
        return {"ok": True, "path": str(path)}

    @app.post("/control/system/open-workspace/{workspace_id}", include_in_schema=False)
    def open_workspace(workspace_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        workspace = next((item for item in service.workspaces.list() if item.workspace_id == workspace_id), None)
        if workspace is None:
            raise HTTPException(status_code=404, detail="WORKSPACE_NOT_FOUND")
        try:
            _open_local_path(workspace.root)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "path": str(workspace.root)}

    @app.post("/control/system/open-session/{session_id}", include_in_schema=False)
    def open_session(session_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        session = next((item for item in service.sessions.list() if item.session_id == session_id), None)
        if session is None:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        project = Path(session.project_file).expanduser() if session.project_file else None
        if project is None:
            raise HTTPException(status_code=400, detail="SESSION_HAS_NO_PROJECT_PATH")
        target = project.parent if project.suffix else project
        try:
            _open_local_path(target)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "path": str(target)}

    @app.get("/control/recoveries/{recovery_id}", include_in_schema=False)
    def recovery_report(recovery_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            return JSONResponse(service.recoveries.read(recovery_id))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="RECOVERY_NOT_FOUND")

    @app.get("/control/evidence/{evidence_id}", include_in_schema=False)
    def evidence(evidence_id: str, ai_bridge_token: str | None = Cookie(default=None)):
        check_cookie(ai_bridge_token)
        try:
            return JSONResponse(service.evidence.read(evidence_id))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")
