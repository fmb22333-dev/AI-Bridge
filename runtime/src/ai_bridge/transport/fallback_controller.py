from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_bridge.core.service import BridgeService
from ai_bridge.security.secret_store import SecretStore
from .runner import TransportRunner
from .supabase_bus import SupabaseBusConfig, SupabaseBusTransport


@dataclass(frozen=True)
class SupabaseFallbackConfig:
    project_url: str
    bridge_id: str
    table: str = "ai_bridge_commands"
    poll_interval_seconds: float = 1.0
    kind: str = "supabase_fallback"


class SupabaseFallbackController:
    """Runs Supabase as an always-on secondary ingress beside GitHub."""

    SECRET_NAME = "supabase_bus"

    def __init__(
        self,
        *,
        service: BridgeService,
        data_dir: Path,
        runtime_state: dict,
        secret_store: SecretStore,
        bridge_id_hint: str,
        transport_factory=None,
    ) -> None:
        self.service = service
        self.data_dir = Path(data_dir)
        self.runtime_state = runtime_state
        self.secret_store = secret_store
        self.bridge_id_hint = str(bridge_id_hint or "").strip()
        self.transport_factory = transport_factory
        self.config_path = self.data_dir / "fallback_transport.json"
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._transport = None
        self.runtime_state.setdefault(
            "fallback_transport",
            {"configured": False, "status": "unconfigured", "kind": "none"},
        )

    def _state(
        self,
        status: str,
        config: SupabaseFallbackConfig | None = None,
        detail: str | None = None,
    ) -> dict:
        state = {
            "configured": config is not None,
            "status": status,
            "kind": config.kind if config is not None else "none",
        }
        if config is not None:
            state.update(
                {
                    "project_url": config.project_url,
                    "bridge_id": config.bridge_id,
                    "table": config.table,
                    "poll_interval_seconds": config.poll_interval_seconds,
                    "credential_saved": bool(self.secret_store.get(self.SECRET_NAME)),
                    "parallel_with_primary": True,
                }
            )
        if detail:
            state["detail"] = detail
        return state

    @staticmethod
    def _normalize(config: SupabaseFallbackConfig) -> SupabaseFallbackConfig:
        return SupabaseFallbackConfig(
            project_url=str(config.project_url or "").strip().rstrip("/"),
            bridge_id=str(config.bridge_id or "").strip(),
            table=str(config.table or "ai_bridge_commands").strip(),
            poll_interval_seconds=max(0.25, float(config.poll_interval_seconds)),
        )

    def _save_config(self, config: SupabaseFallbackConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.config_path.chmod(0o600)
        except OSError:
            pass

    def _load_config(self) -> SupabaseFallbackConfig | None:
        if not self.config_path.exists():
            return None
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        if data.get("kind") != "supabase_fallback":
            return None
        return self._normalize(
            SupabaseFallbackConfig(
                project_url=str(data["project_url"]),
                bridge_id=str(data["bridge_id"]),
                table=str(data.get("table") or "ai_bridge_commands"),
                poll_interval_seconds=float(data.get("poll_interval_seconds") or 1.0),
            )
        )

    def _make_transport(self, config: SupabaseFallbackConfig, secret_key: str):
        if self.transport_factory:
            return self.transport_factory(config, secret_key)
        return SupabaseBusTransport(
            SupabaseBusConfig(
                project_url=config.project_url,
                secret_key=secret_key,
                bridge_id=config.bridge_id,
                table=config.table,
            )
        )

    def configure(self, config: SupabaseFallbackConfig, secret_key: str) -> dict:
        config = self._normalize(config)
        secret_key = str(secret_key or "").strip()
        if not config.project_url:
            raise ValueError("Supabase project URL is required")
        if not config.bridge_id:
            raise ValueError("Supabase bridge ID is required")
        if not secret_key:
            raise ValueError("Supabase secret key is required")

        transport = self._make_transport(config, secret_key)
        health = transport.health()
        if not health.ok:
            raise RuntimeError("Supabase fallback connection failed: " + health.detail)

        self.stop()
        self.secret_store.set(self.SECRET_NAME, secret_key)
        self._save_config(config)
        self._transport = transport
        self.runtime_state["fallback_transport"] = self._state("connected", config)
        self._start_loop(config, transport)
        return dict(self.runtime_state["fallback_transport"])

    def _start_loop(self, config: SupabaseFallbackConfig, transport) -> None:
        stop = threading.Event()
        self._stop = stop
        runner = TransportRunner(self.service, transport)

        def loop() -> None:
            last_health = 0.0
            while not stop.is_set():
                try:
                    now = time.monotonic()
                    if now - last_health > 300.0:
                        health = transport.health()
                        last_health = now
                        if not health.ok:
                            self.runtime_state["fallback_transport"] = self._state(
                                "error", config, health.detail
                            )
                            stop.wait(config.poll_interval_seconds)
                            continue
                    runner.poll_once()
                    self.runtime_state["fallback_transport"] = self._state(
                        "connected", config
                    )
                except Exception as exc:
                    self.runtime_state["fallback_transport"] = self._state(
                        "error",
                        config,
                        f"{type(exc).__name__}: {exc}",
                    )
                stop.wait(config.poll_interval_seconds)

        self._thread = threading.Thread(
            target=loop,
            name="AI-Bridge-SupabaseFallback",
            daemon=True,
        )
        self._thread.start()

    def start_saved_or_environment(self) -> bool:
        env_url = str(os.environ.get("AI_BRIDGE_SUPABASE_URL") or "").strip()
        env_key = str(os.environ.get("AI_BRIDGE_SUPABASE_SECRET_KEY") or "").strip()
        env_bridge_id = str(
            os.environ.get("AI_BRIDGE_SUPABASE_BRIDGE_ID")
            or self.bridge_id_hint
            or ""
        ).strip()
        env_table = str(
            os.environ.get("AI_BRIDGE_SUPABASE_TABLE") or "ai_bridge_commands"
        ).strip()
        env_poll = float(
            os.environ.get("AI_BRIDGE_SUPABASE_POLL_INTERVAL") or "1.0"
        )

        config = self._load_config()
        secret_key = self.secret_store.get(self.SECRET_NAME)

        if config is None and env_url and env_key:
            try:
                self.configure(
                    SupabaseFallbackConfig(
                        project_url=env_url,
                        bridge_id=env_bridge_id,
                        table=env_table,
                        poll_interval_seconds=env_poll,
                    ),
                    env_key,
                )
                return True
            except Exception as exc:
                self.runtime_state["fallback_transport"] = self._state(
                    "error", None, f"{type(exc).__name__}: {exc}"
                )
                return False

        if config is None:
            self.runtime_state["fallback_transport"] = self._state("unconfigured")
            return False

        if not secret_key and env_key:
            secret_key = env_key
            self.secret_store.set(self.SECRET_NAME, env_key)

        if not secret_key:
            self.runtime_state["fallback_transport"] = self._state(
                "credential_missing", config
            )
            return False

        try:
            self.configure(config, secret_key)
            return True
        except Exception as exc:
            self.runtime_state["fallback_transport"] = self._state(
                "error", config, f"{type(exc).__name__}: {exc}"
            )
            return False

    def stop(self) -> None:
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
