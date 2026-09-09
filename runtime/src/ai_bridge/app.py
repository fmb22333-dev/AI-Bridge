from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from ai_bridge.config import default_data_dir, ensure_connection_config, load_workspaces
from ai_bridge.adapters.bridge_admin import ADAPTER_NAME as BRIDGE_ADMIN_NAME, BridgeAdminExecutor, SYSTEM_WORKSPACE, descriptor as bridge_admin_descriptor
from ai_bridge.core.service import BridgeService
from ai_bridge.core.execution_policy import ExecutionPolicyStore
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.security.secret_store import default_secret_store
from ai_bridge.transport.local_api import create_app
from ai_bridge.transport.remote_config import GitHubRemoteConfig, default_bridge_id
from ai_bridge.transport.remote_controller import RemoteController
from ai_bridge.web.routes import install_control_routes


def build_runtime(*, data_dir: Path, port: int):
    data_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}"
    connection = ensure_connection_config(data_dir, url=url)
    workspaces_file = data_dir / "workspaces.json"
    workspaces = WorkspaceRegistry()
    for item in load_workspaces(workspaces_file):
        try:
            workspaces.register(item["workspace_id"], Path(item["root"]))
        except (KeyError, ValueError, FileNotFoundError, NotADirectoryError):
            pass
    system_root = Path(os.environ.get("AI_BRIDGE_ROOT") or data_dir).resolve()
    try:
        workspaces.register(SYSTEM_WORKSPACE, system_root)
    except ValueError:
        pass
    service = BridgeService(
        db=BridgeDB(data_dir / "bridge.db"),
        workspaces=workspaces,
        execution_policy=ExecutionPolicyStore(data_dir / "execution_policy.json"),
    )
    service.adapter_registry.register(bridge_admin_descriptor(), replace=True)

    def _plugin_status():
        return service.plugins.status(live_sessions=service.active_sessions())

    def _plugin_install(host_id: str):
        host_id = str(host_id or "").strip().lower()
        host_running = any(
            session.adapter == host_id and service.sessions.is_active(session.session_id)
            for session in service.sessions.list(adapter=host_id)
        )
        return service.plugins.install(host_id, host_running=host_running)

    def _plugin_install_all():
        return service.plugins.install_all(live_sessions=service.active_sessions())

    def _plugin_restart(arguments: dict):
        host_id = str(arguments.get("host_id") or "").strip().lower()
        session_id = str(arguments.get("session_id") or "").strip()
        workspace = str(arguments.get("workspace") or "").strip()
        if not host_id:
            raise ValueError("host_id is required")
        if not session_id:
            raise ValueError("session_id is required")
        if not workspace:
            user_workspaces = [
                item.workspace_id
                for item in service.workspaces.list()
                if not item.workspace_id.startswith("__")
            ]
            if "Bridge" in user_workspaces:
                workspace = "Bridge"
            elif user_workspaces:
                workspace = user_workspaces[0]
            else:
                raise ValueError("NO_USER_WORKSPACE_AVAILABLE")
        return service.restart_host_for_plugin_update(
            host_id=host_id,
            session_id=session_id,
            workspace=workspace,
            force_restart=bool(arguments.get("force_restart", False)),
        )


    def _host_force_recover(arguments: dict):
        host_id = str(arguments.get("host_id") or "").strip().lower()
        session_id = str(arguments.get("session_id") or "").strip() or None
        project_file = str(arguments.get("project_file") or "").strip() or None
        raw_pid = arguments.get("pid")
        pid = int(raw_pid) if raw_pid is not None else None
        workspace = str(arguments.get("workspace") or "").strip()
        if not host_id:
            raise ValueError("host_id is required")
        if not workspace:
            user_workspaces = [
                item.workspace_id
                for item in service.workspaces.list()
                if not item.workspace_id.startswith("__")
            ]
            if "Bridge" in user_workspaces:
                workspace = "Bridge"
            elif user_workspaces:
                workspace = user_workspaces[0]
            else:
                raise ValueError("NO_USER_WORKSPACE_AVAILABLE")
        return service.force_recover_host(
            host_id=host_id,
            workspace=workspace,
            session_id=session_id,
            pid=pid,
            project_file=project_file,
            checkpoint_id=(
                str(arguments.get("checkpoint_id") or "").strip() or None
            ),
            allow_saved_project_fallback=bool(
                arguments.get("allow_saved_project_fallback", False)
            ),
            session_wait_seconds=float(
                arguments.get("session_wait_seconds") or 120.0
            ),
            restore_timeout_seconds=float(
                arguments.get("restore_timeout_seconds") or 60.0
            ),
            source_command_id=(
                str(arguments.get("source_command_id") or "").strip() or None
            ),
            source_operation=(
                str(arguments.get("source_operation") or "").strip() or None
            ),
        )

    def _project_resume(index: dict, project: str | None, history_limit: int):
        return service.project_resume_local(
            index=index,
            project=project,
            history_limit=history_limit,
        )

    service.register_executor(
        BRIDGE_ADMIN_NAME,
        BridgeAdminExecutor(
            data_dir=data_dir,
            plugin_status_provider=_plugin_status,
            plugin_install_handler=_plugin_install,
            plugin_install_all_handler=_plugin_install_all,
            plugin_restart_handler=_plugin_restart,
            host_recover_handler=_host_force_recover,
            project_resume_handler=_project_resume,
        ),
    )
    app = create_app(service, auth_token=connection["token"])
    runtime_state = {
        "remote": {
            "configured": False,
            "status": "unconfigured",
            "kind": "none",
            "suggested_bridge_id": default_bridge_id(),
        }
    }
    app.state.remote_controller = None
    web_root = Path(__file__).parent / "web"
    install_control_routes(
        app,
        service,
        auth_token=connection["token"],
        web_root=web_root,
        workspaces_file=workspaces_file,
        runtime_state=runtime_state,
    )
    return app, service, connection, runtime_state


def _install_remote_controller(
    app,
    service: BridgeService,
    data_dir: Path,
    runtime_state: dict,
) -> RemoteController:
    controller = RemoteController(
        service=service,
        data_dir=data_dir,
        runtime_state=runtime_state,
        secret_store=default_secret_store(data_dir),
    )
    app.state.remote_controller = controller

    # One-time migration from older environment-variable setup.
    repository = os.environ.get("AI_BRIDGE_GITHUB_REPOSITORY")
    token = os.environ.get("AI_BRIDGE_GITHUB_TOKEN")
    bridge_id = os.environ.get("AI_BRIDGE_GITHUB_BRIDGE_ID")
    if repository and token and bridge_id and not (data_dir / "remote.json").exists():
        try:
            controller.configure_github(
                GitHubRemoteConfig(
                    repository=repository,
                    branch=os.environ.get("AI_BRIDGE_GITHUB_BRANCH", "main"),
                    bridge_id=bridge_id,
                ),
                token,
            )
            return controller
        except Exception as exc:
            runtime_state["remote"] = {
                "configured": False,
                "status": "error",
                "kind": "none",
                "detail": f"Environment migration failed: {type(exc).__name__}: {exc}",
                "suggested_bridge_id": default_bridge_id(),
            }
            return controller

    controller.start_saved()
    if not runtime_state.get("remote", {}).get("bridge_id"):
        runtime_state.setdefault("remote", {})["suggested_bridge_id"] = default_bridge_id()
    return controller


def _resolve_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError(f"No free localhost port found in range {preferred}-{preferred + 19}")


def main() -> None:
    preferred_port = int(os.environ.get("AI_BRIDGE_PORT", "8765"))
    port = _resolve_port(preferred_port)
    data_dir = default_data_dir()
    app, service, connection, runtime_state = build_runtime(data_dir=data_dir, port=port)
    _install_remote_controller(app, service, data_dir, runtime_state)

    remote = runtime_state.get("remote", {})
    if remote.get("configured") and remote.get("status") != "credential_missing":
        target = "/"
    else:
        target = "/setup"
    url = (
        f"http://127.0.0.1:{port}/control/bootstrap"
        f"?token={connection['token']}&target={target}"
    )
    threading.Thread(
        target=lambda: (time.sleep(0.8), webbrowser.open(url)),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
