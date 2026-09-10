from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_bridge.core.service import BridgeService
from ai_bridge.security.secret_store import SecretStore
from ai_bridge.transport.github_bus import GitHubBusConfig, GitHubBusTransport
from ai_bridge.transport.remote_config import (
    GitHubRemoteConfig,
    delete_remote_config,
    load_remote_config,
    save_remote_config,
)
from ai_bridge.transport.runner import TransportRunner


class RemoteConfigurationError(RuntimeError):
    pass


class RemoteController:
    GITHUB_SECRET = "github_bus"

    def __init__(
        self,
        *,
        service: BridgeService,
        data_dir: Path,
        runtime_state: dict,
        secret_store: SecretStore,
        poll_interval: float = 0.5,
        transport_factory=None,
    ) -> None:
        self.service = service
        self.data_dir = Path(data_dir)
        self.runtime_state = runtime_state
        self.secret_store = secret_store
        self.poll_interval = max(0.25, float(poll_interval))
        self.config_path = self.data_dir / "remote.json"
        self.disabled_path = self.data_dir / "remote.disabled"
        self.transport_factory = transport_factory
        self._lock = threading.RLock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._transport = None
        self._last_presence_hash: str | None = None
        self._activity = {
            "last_poll_at": None,
            "last_poll_finished_at": None,
            "last_command_received_at": None,
            "current_execution": None,
        }

    def _activity_snapshot(self) -> dict:
        current = self._activity.get("current_execution")
        return {
            "last_poll_at": self._activity.get("last_poll_at"),
            "last_poll_finished_at": self._activity.get("last_poll_finished_at"),
            "last_command_received_at": self._activity.get("last_command_received_at"),
            "current_execution": dict(current) if isinstance(current, dict) else None,
        }

    def _reset_activity_locked(self) -> None:
        self._activity = {
            "last_poll_at": None,
            "last_poll_finished_at": None,
            "last_command_received_at": None,
            "current_execution": None,
        }
        remote = self.runtime_state.get("remote")
        if isinstance(remote, dict):
            remote["activity"] = self._activity_snapshot()

    def _on_transport_activity(self, event: str, payload: dict) -> None:
        payload = dict(payload or {})
        at = str(payload.get("at") or datetime.now(timezone.utc).isoformat())
        with self._lock:
            if event == "poll_started":
                self._activity["last_poll_at"] = at
            elif event == "poll_finished":
                self._activity["last_poll_finished_at"] = at
            elif event == "command_started":
                self._activity["last_command_received_at"] = at
                self._activity["current_execution"] = {
                    "command_id": payload.get("command_id"),
                    "operation": payload.get("operation"),
                    "adapter": payload.get("adapter"),
                    "workspace": payload.get("workspace"),
                    "started_at": at,
                }
            elif event == "command_finished":
                current = self._activity.get("current_execution")
                command_id = payload.get("command_id")
                if not isinstance(current, dict) or current.get("command_id") == command_id:
                    self._activity["current_execution"] = None
            self.runtime_state.setdefault("remote", {})["activity"] = self._activity_snapshot()

    def _presence_core(self, bridge_id: str, transport=None) -> dict:
        sessions = []
        for session in self.service.sessions.list():
            status = self.service.sessions.status(session.session_id)
            if status.state != "connected":
                continue
            sessions.append(
                {
                    "session_id": session.session_id,
                    "adapter": session.adapter,
                    "host_version": session.host_version,
                    "project_file": session.project_file,
                    "pid": session.pid,
                    "state": status.state,
                }
            )
        sessions.sort(key=lambda item: (item["adapter"], item["session_id"]))
        return {
            "protocol": "bridge/1",
            "bridge_id": bridge_id,
            "bridge_version": "0.2.6.51",
            "message_transport": (
                transport.message_state()
                if transport is not None and hasattr(transport, "message_state")
                else {"mode": "contents"}
            ),
            "write_blocked": self.service.emergency_stop.write_blocked,
            "workspaces": [
                workspace.workspace_id
                for workspace in sorted(self.service.workspaces.list(), key=lambda item: item.workspace_id)
                if not workspace.workspace_id.startswith("__")
            ],
            "sessions": sessions,
        }

    def _effective_poll_interval(self, transport=None) -> float:
        base = self.poll_interval
        mode = "contents"
        if transport is not None and hasattr(transport, "message_state"):
            try:
                mode = str(transport.message_state().get("mode") or "contents")
            except Exception:
                mode = "contents"

        if mode == "issue_mailbox_v3":
            base = min(base, 0.25)

        if transport is None or not hasattr(transport, "rate_snapshot"):
            return base
        try:
            rate = transport.rate_snapshot()
            remaining = rate.get("remaining")
            limit = rate.get("limit")
            reset = rate.get("reset")
            polls = int(rate.get("polls") or 0)
            not_modified_ratio = float(rate.get("not_modified_ratio") or 0.0)
            remaining = int(remaining) if remaining is not None else None
            limit = int(limit) if limit is not None else None
            reset = int(reset) if reset is not None else None
        except Exception:
            return base

        if mode == "issue_mailbox_v3" and polls >= 20 and not_modified_ratio < 0.70:
            base = max(base, 0.5)

        if remaining is None or limit in (None, 0):
            return base
        ratio = remaining / max(limit, 1)
        if remaining <= 100 or ratio <= 0.02:
            if reset and reset > int(time.time()):
                seconds = max(1.0, reset - time.time())
                return min(30.0, max(5.0, seconds / max(remaining, 1)))
            return 10.0
        if remaining <= 500 or ratio <= 0.10:
            return max(base, 2.0)
        if remaining <= 1000 or ratio <= 0.20:
            return max(base, 1.0)
        return base

    def public_state(self, status: str, config: GitHubRemoteConfig | None = None, detail: str | None = None) -> dict:
        state = {
            "configured": config is not None,
            "status": status,
            "kind": "github_bus" if config is not None else "none",
        }
        if config is not None:
            state.update(
                {
                    "repository": config.repository,
                    "branch": config.branch,
                    "bridge_id": config.bridge_id,
                    "credential_saved": bool(self.secret_store.get(self.GITHUB_SECRET)),
                    "poll_floor_seconds": (
                        0.25
                        if self._transport is not None
                        and hasattr(self._transport, "message_state")
                        and self._transport.message_state().get("mode") == "issue_mailbox_v3"
                        else self.poll_interval
                    ),
                    "poll_interval_seconds": self._effective_poll_interval(self._transport),
                    "github_rate": (
                        self._transport.rate_snapshot()
                        if self._transport is not None and hasattr(self._transport, "rate_snapshot")
                        else {}
                    ),
                    "message_transport": (
                        self._transport.message_state()
                        if self._transport is not None and hasattr(self._transport, "message_state")
                        else {"mode": "contents"}
                    ),
                    "activity": self._activity_snapshot(),
                }
            )
        if detail:
            state["detail"] = detail
        return state

    def _make_transport(self, config: GitHubRemoteConfig, token: str):
        if self.transport_factory:
            return self.transport_factory(config, token)
        return GitHubBusTransport(
            GitHubBusConfig(
                repository=config.repository,
                token=token,
                bridge_id=config.bridge_id,
                branch=config.branch,
                path_prefix=".ai-bridge",
            )
        )

    def configure_github(self, config: GitHubRemoteConfig, token: str) -> dict:
        repository = config.repository.strip().strip("/")
        branch = config.branch.strip()
        bridge_id = config.bridge_id.strip()
        token = token.strip()
        if repository.count("/") != 1 or any(not part for part in repository.split("/")):
            raise RemoteConfigurationError("Repository must be owner/name")
        if not branch:
            raise RemoteConfigurationError("Branch is required")
        if not bridge_id:
            raise RemoteConfigurationError("Bridge ID is required")
        if not token:
            raise RemoteConfigurationError("GitHub token is required")

        normalized = GitHubRemoteConfig(repository=repository, branch=branch, bridge_id=bridge_id)
        transport = self._make_transport(normalized, token)
        health = transport.health()
        if not health.ok:
            raise RemoteConfigurationError("GitHub connection failed: " + health.detail)
        try:
            if hasattr(transport, "initialize_message_mode"):
                transport.initialize_message_mode()
            self._publish_presence_if_changed(transport, normalized, force=True)
        except Exception as exc:
            raise RemoteConfigurationError(
                f"GitHub write test failed: {type(exc).__name__}: {exc}"
            ) from exc

        with self._lock:
            self._stop_current_locked()
            self.secret_store.set(self.GITHUB_SECRET, token)
            save_remote_config(self.config_path, normalized)
            try:
                self.disabled_path.unlink()
            except FileNotFoundError:
                pass
            self._transport = transport
            self._reset_activity_locked()
            self.runtime_state["remote"] = self.public_state("connected", normalized)
            self._start_github_loop_locked(normalized, transport)
        return dict(self.runtime_state["remote"])

    configure = configure_github

    def _publish_presence_if_changed(self, transport, config: GitHubRemoteConfig, force: bool = False) -> None:
        core = self._presence_core(config.bridge_id, transport)
        raw = json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
        fingerprint = hashlib.sha256(raw).hexdigest()
        if not force and fingerprint == self._last_presence_hash:
            return
        payload = dict(core)
        payload["published_at"] = datetime.now(timezone.utc).isoformat()
        transport.publish_presence(payload)
        self._last_presence_hash = fingerprint

    def _start_github_loop_locked(self, config: GitHubRemoteConfig, transport) -> None:
        stop = threading.Event()
        self._stop = stop
        runner = TransportRunner(
            self.service,
            transport,
            activity_observer=self._on_transport_activity,
        )

        def loop() -> None:
            last_health = 0.0
            while not stop.is_set():
                try:
                    now = time.monotonic()
                    if now - last_health > 300.0:
                        health = transport.health()
                        last_health = now
                        if not health.ok:
                            self.runtime_state["remote"] = self.public_state("error", config, health.detail)
                            stop.wait(self._effective_poll_interval(transport))
                            continue
                    runner.poll_once()
                    self._publish_presence_if_changed(transport, config)
                    self.runtime_state["remote"] = self.public_state("connected", config)
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    self.runtime_state["remote"] = self.public_state("error", config, detail)
                    if "403" in detail or "429" in detail:
                        if stop.wait(60.0):
                            break
                        continue
                stop.wait(self._effective_poll_interval(transport))

        self._thread = threading.Thread(target=loop, name="AI-Bridge-GitHubBus", daemon=True)
        self._thread.start()

    def start_saved(self) -> bool:
        if self.disabled_path.exists():
            self.runtime_state["remote"] = {
                "configured": False,
                "status": "disabled",
                "kind": "none",
            }
            return False
        config = load_remote_config(self.config_path)
        if config is None:
            self.runtime_state["remote"] = {
                "configured": False,
                "status": "unconfigured",
                "kind": "none",
            }
            return False
        token = self.secret_store.get(self.GITHUB_SECRET)
        if not token:
            self.runtime_state["remote"] = self.public_state("credential_missing", config)
            return False
        try:
            self.configure_github(config, token)
            return True
        except Exception as exc:
            self.runtime_state["remote"] = self.public_state(
                "error", config, f"{type(exc).__name__}: {exc}"
            )
            return False

    def disconnect(self) -> None:
        with self._lock:
            self._stop_current_locked()
            delete_remote_config(self.config_path)
            self.secret_store.delete(self.GITHUB_SECRET)
            self.disabled_path.write_text("disabled by user", encoding="utf-8")
            self.runtime_state["remote"] = {
                "configured": False,
                "status": "disabled",
                "kind": "none",
            }

    def _stop_current_locked(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if (
            self._thread is not None
            and self._thread.is_alive()
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=2.0)
        self._stop = None
        self._thread = None
        self._transport = None
        self._last_presence_hash = None
