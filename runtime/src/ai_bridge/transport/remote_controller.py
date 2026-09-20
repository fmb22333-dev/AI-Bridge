from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_bridge.core.service import BridgeService
from ai_bridge.runtime_version import runtime_version
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


def _classify_github_transport_error(exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}".casefold()
    if "429" in detail or "rate limit" in detail:
        return "rate_limited"
    if "401" in detail or "bad credentials" in detail:
        return "auth_degraded"
    if "403" in detail:
        return "auth_degraded"
    return "error"


def _auth_retry_delay(failure_count: int) -> float:
    count = max(1, int(failure_count))
    return min(120.0, 5.0 * (2 ** min(count - 1, 5)))


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
        self._active_executions: dict[str, dict] = {}

    def _activity_snapshot(self) -> dict:
        active = [dict(item) for item in self._active_executions.values()]
        active.sort(key=lambda item: str(item.get("started_at") or ""))
        current = active[-1] if active else None
        return {
            "last_poll_at": self._activity.get("last_poll_at"),
            "last_poll_finished_at": self._activity.get("last_poll_finished_at"),
            "last_command_received_at": self._activity.get("last_command_received_at"),
            "current_execution": current,
            "active_execution_count": len(active),
            "active_executions": active[-8:],
        }

    def _reset_activity_locked(self) -> None:
        self._active_executions.clear()
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
                command_id = str(payload.get("command_id") or "")
                execution = {
                    "command_id": command_id or None,
                    "operation": payload.get("operation"),
                    "adapter": payload.get("adapter"),
                    "workspace": payload.get("workspace"),
                    "started_at": at,
                }
                if command_id:
                    self._active_executions[command_id] = execution
                self._activity["current_execution"] = execution
            elif event == "command_finished":
                command_id = str(payload.get("command_id") or "")
                if command_id:
                    self._active_executions.pop(command_id, None)
                remaining = list(self._active_executions.values())
                remaining.sort(key=lambda item: str(item.get("started_at") or ""))
                self._activity["current_execution"] = dict(remaining[-1]) if remaining else None
            self.runtime_state.setdefault("remote", {})["activity"] = self._activity_snapshot()

    @staticmethod
    def _presence_transport_state(transport) -> dict:
        if transport is None or not hasattr(transport, "message_state"):
            return {"mode": "contents"}
        try:
            state = transport.message_state()
        except Exception:
            return {"mode": "contents"}
        if not isinstance(state, dict):
            return {"mode": "contents"}
        # Presence is durable semantic state, not a poll trace. Volatile ingress
        # diagnostics (available/not_modified/not_polled) stay in public_state.
        stable_keys = (
            "mode",
            "channel_protocol",
            "multi_channel",
            "channel_discovery",
            "channel_window",
            "issue_number",
            "mailbox_comment_id",
            "comment_write_ok",
            "multi_ingress",
            "role",
            "github_primary_ingress",
            "github_fallback_ingress",
            "github_active_ingress",
            "compatibility",
        )
        return {key: state[key] for key in stable_keys if key in state}

    @staticmethod
    def _capability_digest(capabilities) -> str:
        rows = []
        for capability in capabilities or ():
            if hasattr(capability, "model_dump"):
                payload = capability.model_dump(mode="json")
            elif isinstance(capability, dict):
                payload = dict(capability)
            else:
                payload = {"value": str(capability)}
            rows.append(payload)
        rows.sort(
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        raw = json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _presence_core(self, bridge_id: str, transport=None) -> dict:
        sessions = []
        for session in self.service.sessions.list():
            status = self.service.sessions.status(session.session_id)
            if status.state not in {"connected", "busy_unknown"}:
                continue
            sessions.append(
                {
                    "session_id": session.session_id,
                    "adapter": session.adapter,
                    "adapter_version": session.adapter_version,
                    # Current Host adapters report the loaded plugin build through
                    # adapter_version. Keep the alias explicit until Host protocol
                    # grows an independent plugin_version/build_sha identity.
                    "plugin_version": session.adapter_version,
                    "build_sha": None,
                    "capability_digest": self._capability_digest(session.capabilities),
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
            "bridge_version": runtime_version(),
            "message_transport": self._presence_transport_state(transport),
            "liveness": {
                "status_semantics": "last_published_durable_state",
                "live_probe": {"adapter": "bridge_transport", "operation": "transport.ping"},
            },
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

    @staticmethod
    def _should_rebuild_transport(failure_mode: str, failure_count: int) -> bool:
        return str(failure_mode) == "error" and int(failure_count) >= 3

    def _rebuild_transport(self, config: GitHubRemoteConfig, runner: TransportRunner):
        token = self.secret_store.get(self.GITHUB_SECRET)
        if not token:
            raise RuntimeError("GitHub credential missing during transport rebuild")
        replacement = self._make_transport(config, token)
        try:
            health = replacement.health()
            if not health.ok:
                raise RuntimeError("GitHub transport rebuild health failed: " + str(health.detail))
            if hasattr(replacement, "initialize_message_mode"):
                replacement.initialize_message_mode()
            old = runner.replace_transport(replacement)
            with self._lock:
                self._transport = replacement
        except Exception:
            client = getattr(replacement, "client", None)
            if client is not None and hasattr(client, "close"):
                try:
                    client.close()
                except Exception:
                    pass
            raise

        old_client = getattr(old, "client", None)
        if old_client is not None and hasattr(old_client, "close"):
            try:
                old_client.close()
            except Exception:
                pass
        return replacement

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
            nonlocal transport
            last_health = 0.0
            auth_failures = 0
            generic_failures = 0
            try:
                while not stop.is_set():
                    try:
                        now = time.monotonic()
                        if now - last_health > 300.0:
                            health = transport.health()
                            last_health = now
                            if not health.ok:
                                raise RuntimeError(health.detail or "GitHub health check failed")
                        runner.poll_once()
                        self._publish_presence_if_changed(transport, config)
                        auth_failures = 0
                        generic_failures = 0
                        self.runtime_state["remote"] = self.public_state("connected", config)
                    except Exception as exc:
                        detail = f"{type(exc).__name__}: {exc}"
                        failure_mode = _classify_github_transport_error(exc)
                        if failure_mode == "auth_degraded":
                            auth_failures += 1
                            delay = _auth_retry_delay(auth_failures)
                            state = self.public_state("auth_degraded", config, detail)
                            state["retry_in_seconds"] = delay
                            state["auth_failure_count"] = auth_failures
                            state["last_auth_error_at"] = datetime.now(timezone.utc).isoformat()
                            self.runtime_state["remote"] = state
                            if stop.wait(delay):
                                break
                            continue
                        if failure_mode == "rate_limited":
                            self.runtime_state["remote"] = self.public_state("rate_limited", config, detail)
                            if stop.wait(60.0):
                                break
                            continue
                        generic_failures += 1
                        if self._should_rebuild_transport(failure_mode, generic_failures):
                            try:
                                transport = self._rebuild_transport(config, runner)
                                last_health = 0.0
                                generic_failures = 0
                                self.runtime_state["remote"] = self.public_state(
                                    "connected", config, "GitHub transport rebuilt after repeated ordinary errors"
                                )
                                continue
                            except Exception as rebuild_exc:
                                detail += f"; rebuild failed: {type(rebuild_exc).__name__}: {rebuild_exc}"
                        self.runtime_state["remote"] = self.public_state("error", config, detail)
                    stop.wait(self._effective_poll_interval(transport))
            finally:
                runner.shutdown(wait=False)

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
