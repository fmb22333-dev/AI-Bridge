from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


DEFAULT_HOST_PLUGIN_MANIFEST = {
    "schema_version": "1.0",
    "hosts": [
        {
            "id": "houdini",
            "display_name": "Houdini",
            "state": "ready",
            "bundled": True,
            "adapter_dir": "houdini_adapter",
            "installer": "install_houdini_adapter.py",
            "adapter_module": "ai_bridge_houdini",
            "supports_install": True,
            "supports_restart_apply": True,
        },
        {
            "id": "blender",
            "display_name": "Blender",
            "state": "scaffold",
            "bundled": True,
            "adapter_dir": "blender_adapter",
            "supports_install": False,
            "supports_restart_apply": False,
        },
        {
            "id": "unreal",
            "display_name": "Unreal Engine",
            "state": "scaffold",
            "bundled": True,
            "adapter_dir": "unreal_adapter",
            "supports_install": False,
            "supports_restart_apply": False,
        },
    ],
}


class HostPluginManager:
    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = (
            Path(runtime_root).resolve()
            if runtime_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self.manifest_path = self.runtime_root / "src" / "ai_bridge" / "host_plugins.json"
        self._bundled_cache: dict[str, dict] = {}

    def _manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                hosts = data.get("hosts")
                if isinstance(hosts, list):
                    return data
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_HOST_PLUGIN_MANIFEST))

    def _entry(self, host_id: str) -> dict:
        host_id = str(host_id or "").strip().lower()
        for item in self._manifest().get("hosts") or []:
            if str(item.get("id") or "").strip().lower() == host_id:
                return dict(item)
        raise KeyError(host_id)

    @staticmethod
    def _version_from_init(path: Path) -> str | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        return match.group(1) if match else None

    def _load_installer(self, entry: dict):
        relative = entry.get("installer")
        if not relative:
            raise RuntimeError("installer is not defined")
        path = self.runtime_root / str(relative)
        if not path.exists():
            raise FileNotFoundError(path)
        spec = importlib.util.spec_from_file_location(
            f"ai_bridge_plugin_installer_{entry['id']}",
            path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load installer: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _houdini_bundled(self, entry: dict) -> dict:
        cached = self._bundled_cache.get("houdini")
        if cached is not None:
            return dict(cached)
        source = self.runtime_root / str(entry["adapter_dir"])
        installer = self._load_installer(entry)
        data = {
            "source": source,
            "bundled_version": self._version_from_init(
                source / "python" / "ai_bridge_houdini" / "__init__.py"
            ),
            "bundled_hash": installer._source_hash(source),
        }
        self._bundled_cache["houdini"] = data
        return dict(data)

    def _houdini_status(self, entry: dict, live_sessions: list | None = None) -> dict:
        bundled = self._houdini_bundled(entry)
        source = bundled["source"]
        bundled_version = bundled["bundled_version"]
        installer = self._load_installer(entry)
        user_dir = installer.default_houdini_user_dir().expanduser().resolve()
        target = user_dir / "ai_bridge_houdini"
        marker = target / ".ai_bridge_source.json"
        installed_version = self._version_from_init(
            target / "python" / "ai_bridge_houdini" / "__init__.py"
        )
        bundled_hash = bundled["bundled_hash"]
        installed_hash = None
        if marker.exists():
            try:
                installed_hash = json.loads(marker.read_text(encoding="utf-8")).get("source_hash")
            except Exception:
                installed_hash = None

        sessions = []
        for session in live_sessions or []:
            if getattr(session, "adapter", None) != "houdini":
                continue
            sessions.append({
                "session_id": session.session_id,
                "pid": session.pid,
                "project_file": session.project_file,
                "adapter_version": session.adapter_version,
                "host_version": session.host_version,
            })

        installed = target.exists() and installed_version is not None
        disk_up_to_date = bool(installed_hash and installed_hash == bundled_hash)
        live_versions = sorted({item["adapter_version"] for item in sessions})
        live_up_to_date = bool(sessions) and all(item["adapter_version"] == bundled_version for item in sessions)

        return {
            **entry,
            "bundled_version": bundled_version,
            "installed": installed,
            "installed_version": installed_version,
            "disk_up_to_date": disk_up_to_date,
            "install_path": str(target),
            "package_path": str(user_dir / "packages" / "ai_bridge_houdini.json"),
            "live_sessions": sessions,
            "live_versions": live_versions,
            "live_up_to_date": live_up_to_date,
            "restart_required": bool(sessions and bundled_version and not live_up_to_date),
        }

    def status(self, *, live_sessions: list | None = None) -> dict:
        items = []
        for entry in self._manifest().get("hosts") or []:
            host_id = str(entry.get("id") or "")
            if host_id == "houdini" and entry.get("state") == "ready":
                items.append(self._houdini_status(entry, live_sessions))
            else:
                items.append({
                    **entry,
                    "installed": None,
                    "installed_version": None,
                    "bundled_version": None,
                    "disk_up_to_date": None,
                    "live_sessions": [],
                    "live_versions": [],
                    "live_up_to_date": None,
                    "restart_required": False,
                })
        return {"schema_version": "1.0", "runtime_root": str(self.runtime_root), "hosts": items}

    def install(self, host_id: str, *, host_running: bool = False, force_clean: bool = False) -> dict:
        entry = self._entry(host_id)
        if entry.get("state") != "ready" or not entry.get("supports_install"):
            return {
                "host_id": entry.get("id"),
                "state": entry.get("state"),
                "installed": False,
                "skipped": True,
                "reason": "INSTALLER_NOT_READY",
            }

        if entry["id"] != "houdini":
            raise RuntimeError(f"installer is not implemented for {entry['id']}")

        installer = self._load_installer(entry)
        source = self.runtime_root / str(entry["adapter_dir"])
        result = dict(
            installer.install_adapter(
                installer.default_houdini_user_dir(),
                source=source,
                running_safe=bool(host_running),
                force_clean=bool(force_clean),
            )
        )
        result.update({
            "host_id": "houdini",
            "force_clean": bool(force_clean),
            "state": "ready",
            "bundled_version": self._version_from_init(
                source / "python" / "ai_bridge_houdini" / "__init__.py"
            ),
            "hot_reload_performed": False,
        })
        return result

    def install_all(self, *, live_sessions: list | None = None, running_safe_default: bool = False) -> dict:
        live_sessions = live_sessions or []
        results = []
        for entry in self._manifest().get("hosts") or []:
            host_id = str(entry.get("id") or "")
            running = bool(running_safe_default) or any(
                getattr(session, "adapter", None) == host_id for session in live_sessions
            )
            results.append(self.install(host_id, host_running=running))
        return {
            "results": results,
            "installed_count": sum(1 for item in results if not item.get("skipped") and item.get("host_id")),
            "skipped_count": sum(1 for item in results if item.get("skipped")),
        }
