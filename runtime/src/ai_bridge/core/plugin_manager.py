from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


DEFAULT_HOST_PLUGIN_MANIFEST = {
    "schema_version": "1.1",
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
            "state": "ready",
            "bundled": True,
            "adapter_dir": "unreal_adapter/AIBridgeUE",
            "installer": "unreal_adapter/install_plugin.py",
            "supports_install": True,
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
                if isinstance(data.get("hosts"), list):
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

    @staticmethod
    def _unreal_version(path: Path) -> str | None:
        descriptor = path / "AIBridgeUE.uplugin"
        if not descriptor.is_file():
            return None
        try:
            value = json.loads(descriptor.read_text(encoding="utf-8")).get("VersionName")
        except Exception:
            return None
        return str(value).strip() if value is not None else None

    @staticmethod
    def _project_key(value: str | Path) -> str:
        try:
            return str(Path(value).expanduser().resolve()).casefold()
        except Exception:
            return str(value).replace("/", "\\").casefold()

    def _load_installer(self, entry: dict):
        relative = entry.get("installer")
        if not relative:
            raise RuntimeError("installer is not defined")
        path = self.runtime_root / str(relative)
        if not path.exists():
            raise FileNotFoundError(path)
        spec = importlib.util.spec_from_file_location(
            f"ai_bridge_plugin_installer_{entry['id']}", path
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
        installed_hash = None
        if marker.exists():
            try:
                installed_hash = json.loads(marker.read_text(encoding="utf-8")).get("source_hash")
            except Exception:
                installed_hash = None
        sessions = [
            {
                "session_id": session.session_id,
                "pid": session.pid,
                "project_file": session.project_file,
                "adapter_version": session.adapter_version,
                "host_version": session.host_version,
            }
            for session in (live_sessions or [])
            if getattr(session, "adapter", None) == "houdini"
        ]
        installed = target.exists() and installed_version is not None
        disk_up_to_date = bool(installed_hash and installed_hash == bundled["bundled_hash"])
        live_versions = sorted({item["adapter_version"] for item in sessions})
        live_up_to_date = bool(sessions) and all(
            item["adapter_version"] == bundled_version for item in sessions
        )
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

    def _unreal_bundled(self, entry: dict) -> dict:
        cached = self._bundled_cache.get("unreal")
        if cached is not None:
            return dict(cached)
        source = self.runtime_root / str(entry["adapter_dir"])
        installer = self._load_installer(entry)
        if not (source / "AIBridgeUE.uplugin").is_file():
            raise FileNotFoundError(source / "AIBridgeUE.uplugin")
        data = {
            "source": source,
            "bundled_version": self._unreal_version(source),
            "bundled_hash": installer._source_hash(source),
        }
        self._bundled_cache["unreal"] = data
        return dict(data)

    def _unreal_target_status(self, entry: dict, project_file: str, live_sessions: list | None = None) -> dict:
        bundled = self._unreal_bundled(entry)
        project = Path(project_file).expanduser().resolve()
        target = project.parent / "Plugins" / "AIBridgeUE"
        installer = self._load_installer(entry)
        installed_version = self._unreal_version(target)
        installed = target.is_dir() and installed_version is not None
        installed_hash = installer._source_hash(target) if installed else None
        key = self._project_key(project)
        sessions = [
            {
                "session_id": session.session_id,
                "pid": session.pid,
                "project_file": session.project_file,
                "adapter_version": session.adapter_version,
                "host_version": session.host_version,
            }
            for session in (live_sessions or [])
            if getattr(session, "adapter", None) == "unreal"
            and getattr(session, "project_file", None)
            and self._project_key(session.project_file) == key
        ]
        live_versions = sorted({item["adapter_version"] for item in sessions})
        live_up_to_date = bool(sessions) and all(
            item["adapter_version"] == bundled["bundled_version"] for item in sessions
        )
        disk_up_to_date = bool(installed and installed_hash == bundled["bundled_hash"])
        return {
            "project_file": str(project),
            "install_path": str(target),
            "installed": installed,
            "installed_version": installed_version,
            "disk_up_to_date": disk_up_to_date,
            "bundled_version": bundled["bundled_version"],
            "live_sessions": sessions,
            "live_versions": live_versions,
            "live_up_to_date": live_up_to_date,
            "restart_required": bool(sessions and not live_up_to_date),
        }

    def _unreal_status(
        self,
        entry: dict,
        live_sessions: list | None = None,
        unreal_projects: list[str] | None = None,
    ) -> dict:
        bundled = self._unreal_bundled(entry)
        unique: dict[str, str] = {}
        for value in unreal_projects or []:
            text = str(value or "").strip()
            if text:
                unique[self._project_key(text)] = text
        for session in live_sessions or []:
            if getattr(session, "adapter", None) == "unreal" and getattr(session, "project_file", None):
                unique[self._project_key(session.project_file)] = str(session.project_file)
        targets = [
            self._unreal_target_status(entry, value, live_sessions)
            for _, value in sorted(unique.items())
            if Path(value).expanduser().suffix.lower() == ".uproject"
        ]
        all_live = [item for target in targets for item in target["live_sessions"]]
        payload = {
            **entry,
            "bundled_version": bundled["bundled_version"],
            "targets": targets,
            "live_sessions": all_live,
            "live_versions": sorted({v for target in targets for v in target["live_versions"]}),
            "supports_restart_apply": False,
        }
        if len(targets) == 1:
            payload.update(targets[0])
        elif targets:
            payload.update({
                "installed": all(item["installed"] for item in targets),
                "installed_version": None,
                "disk_up_to_date": all(item["disk_up_to_date"] for item in targets),
                "live_up_to_date": all(
                    item["live_up_to_date"] for item in targets if item["live_sessions"]
                ) if any(item["live_sessions"] for item in targets) else None,
                "restart_required": any(item["restart_required"] for item in targets),
                "project_file": None,
                "install_path": None,
            })
        else:
            payload.update({
                "installed": None,
                "installed_version": None,
                "disk_up_to_date": None,
                "live_up_to_date": None,
                "restart_required": False,
                "project_file": None,
                "install_path": None,
            })
        return payload

    def status(
        self,
        *,
        live_sessions: list | None = None,
        unreal_projects: list[str] | None = None,
    ) -> dict:
        items = []
        for entry in self._manifest().get("hosts") or []:
            host_id = str(entry.get("id") or "")
            if host_id == "houdini" and entry.get("state") == "ready":
                items.append(self._houdini_status(entry, live_sessions))
            elif host_id == "unreal" and entry.get("state") == "ready":
                items.append(self._unreal_status(entry, live_sessions, unreal_projects))
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
        return {"schema_version": "1.1", "runtime_root": str(self.runtime_root), "hosts": items}

    def install(
        self,
        host_id: str,
        *,
        host_running: bool = False,
        force_clean: bool = False,
        project_file: str | None = None,
    ) -> dict:
        entry = self._entry(host_id)
        if entry.get("state") != "ready" or not entry.get("supports_install"):
            return {
                "host_id": entry.get("id"),
                "state": entry.get("state"),
                "installed": False,
                "skipped": True,
                "reason": "INSTALLER_NOT_READY",
            }

        installer = self._load_installer(entry)
        source = self.runtime_root / str(entry["adapter_dir"])
        if entry["id"] == "houdini":
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

        if entry["id"] == "unreal":
            if not project_file:
                raise ValueError("UNREAL_PROJECT_FILE_REQUIRED")
            if host_running:
                raise RuntimeError("TARGET_UNREAL_RUNNING")
            result = dict(
                installer.install_plugin(
                    project_file,
                    source=source,
                    force_clean=bool(force_clean),
                )
            )
            result.update({
                "host_id": "unreal",
                "force_clean": bool(force_clean),
                "state": "ready",
                "hot_reload_performed": False,
                "restart_required": False,
            })
            return result

        raise RuntimeError(f"installer is not implemented for {entry['id']}")

    def install_all(
        self,
        *,
        live_sessions: list | None = None,
        running_safe_default: bool = False,
        unreal_projects: list[str] | None = None,
    ) -> dict:
        live_sessions = live_sessions or []
        results = []
        for entry in self._manifest().get("hosts") or []:
            host_id = str(entry.get("id") or "")
            if host_id == "unreal" and entry.get("state") == "ready":
                results.append({
                    "host_id": "unreal",
                    "state": "ready",
                    "installed": False,
                    "skipped": True,
                    "reason": "USE_PER_PROJECT_INSTALL",
                    "project_count": len(unreal_projects or []),
                })
                continue
            running = bool(running_safe_default) or any(
                getattr(session, "adapter", None) == host_id for session in live_sessions
            )
            results.append(self.install(host_id, host_running=running))
        return {
            "results": results,
            "installed_count": sum(
                1 for item in results
                if not item.get("skipped") and item.get("host_id")
            ),
            "skipped_count": sum(1 for item in results if item.get("skipped")),
        }
