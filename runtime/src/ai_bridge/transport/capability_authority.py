from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


_AUTHORITY_CACHE: dict[str, tuple[str, object]] = {}


class _HipFileProxy:
    def __init__(self, path: str) -> None:
        self._path = str(path or "")

    def path(self) -> str:
        return self._path


class _HouProxy:
    def __init__(self, host_version: str, project_file: str) -> None:
        self._host_version = str(host_version or "")
        self.hipFile = _HipFileProxy(project_file)

    def applicationVersionString(self) -> str:
        return self._host_version


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_houdini_authority() -> tuple[str, object]:
    package_dir = _runtime_root() / "houdini_adapter" / "python" / "ai_bridge_houdini"
    init_file = package_dir / "__init__.py"
    if not init_file.is_file():
        raise FileNotFoundError(f"Bundled Houdini authority is missing: {init_file}")

    cache_key = str(package_dir.resolve())
    cached = _AUTHORITY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    alias = "_ai_bridge_runtime_houdini_" + hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12]
    package = sys.modules.get(alias)
    if package is None:
        spec = importlib.util.spec_from_file_location(
            alias,
            init_file,
            submodule_search_locations=[str(package_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load bundled Houdini authority package")
        package = importlib.util.module_from_spec(spec)
        sys.modules[alias] = package
        try:
            spec.loader.exec_module(package)
        except Exception:
            sys.modules.pop(alias, None)
            raise

    compat_ops = importlib.import_module(f"{alias}.compat_ops")
    version = str(getattr(package, "__version__", "") or "").strip()
    loaded = (version, compat_ops)
    _AUTHORITY_CACHE[cache_key] = loaded
    return loaded


def _session_payload(session) -> dict:
    capabilities = []
    for capability in getattr(session, "capabilities", ()) or ():
        if hasattr(capability, "model_dump"):
            capabilities.append(capability.model_dump(mode="json"))
        elif isinstance(capability, dict):
            capabilities.append(dict(capability))
    return {
        "session_id": str(session.session_id),
        "adapter": str(session.adapter),
        "adapter_version": str(session.adapter_version),
        "host_version": str(session.host_version),
        "project_file": str(session.project_file or ""),
        "capabilities": capabilities,
    }


def _augment_runtime_search_schema(payload: dict) -> None:
    for row in payload.get("results") or []:
        if not isinstance(row, dict) or row.get("kind") != "capability" or row.get("name") != "capability.search":
            continue
        schema = row.setdefault("argument_schema", {})
        if not isinstance(schema, dict):
            continue
        optional = schema.setdefault("optional", {})
        if isinstance(optional, dict):
            optional.setdefault("detail", "compact|full; default compact; full preserves complete authority fields")
            optional.setdefault("force_host", "bool; bypass Runtime-local authority and cache and query the live Host")


def search_runtime_local_capability(command, session, catalog_digest: str) -> tuple[ExecutionResult | None, str]:
    if command.operation != "capability.search":
        return None, "not_capability_search"
    if str(getattr(session, "adapter", "")) != "houdini":
        return None, "unsupported_adapter"

    try:
        bundled_version, compat_ops = _load_houdini_authority()
    except Exception as exc:
        return None, f"local_authority_load_failed:{type(exc).__name__}"

    live_version = str(getattr(session, "adapter_version", "") or "").strip()
    if not bundled_version or bundled_version != live_version:
        return None, "adapter_version_mismatch"

    try:
        session_info = _session_payload(session)
        hou_proxy = _HouProxy(session_info["host_version"], command.project_file or session_info["project_file"])
        payload = compat_ops.capability_search(
            hou_proxy,
            session_info,
            command.arguments.get("query"),
            int(command.arguments.get("limit", 20)),
        )
    except Exception as exc:
        return None, f"local_authority_search_failed:{type(exc).__name__}"

    _augment_runtime_search_schema(payload)
    bridge = payload.setdefault("_bridge", {})
    bridge["capability_authority"] = {
        "mode": "runtime_local",
        "adapter_version": live_version,
        "bundled_adapter_version": bundled_version,
        "catalog_digest": str(catalog_digest),
        "catalog_source": "live_session_registration+bundled_versioned_authority",
    }
    return ExecutionResult(
        command_id=command.command_id,
        status=ExecutionStatus.SUCCESS,
        result=payload,
        evidence_id=f"ev_{command.command_id}",
    ), "runtime_local"
