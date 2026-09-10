from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
BUNDLE = ROOT / "runtime_bundle.zip"
PRODUCT_REPOSITORY = "fmb22333-dev/AI-Bridge"
PRODUCT_REF = "main"
SUPERVISOR_VERSION = "0.1.4"
TEXT_SUFFIXES = {".py", ".json", ".toml", ".html", ".css", ".js", ".md", ".txt", ".bat", ".ps1"}


def _write_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(payload.encode("utf-8"))


def _canonical_payload(path: Path) -> bytes:
    if path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitkeep":
        text = path.read_text(encoding="utf-8")
        return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return path.read_bytes()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _runtime_version() -> str:
    data = tomllib.loads((RUNTIME / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _adapter_version() -> str:
    text = (
        RUNTIME
        / "houdini_adapter"
        / "python"
        / "ai_bridge_houdini"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise RuntimeError("Unable to resolve Houdini Adapter version")
    return match.group(1)


def _build_clean_knowledge() -> dict:
    sys.path.insert(0, str(RUNTIME / "src"))
    from ai_bridge.deployment.knowledge_pack import build_distribution_knowledge

    source = (
        RUNTIME
        / "houdini_adapter"
        / "python"
        / "ai_bridge_houdini"
        / "knowledge"
    )
    destination = ROOT / "knowledge" / "houdini"
    return build_distribution_knowledge(destination, source_root=source)


def _build_runtime_bundle() -> str:
    temp = BUNDLE.with_suffix(".zip.tmp")
    if temp.exists():
        temp.unlink()
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        files = sorted(
            p
            for p in RUNTIME.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix.lower() != ".pyc"
            and p.name != ".gitkeep"
        )
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, _canonical_payload(path))
    temp.replace(BUNDLE)
    return _sha256(BUNDLE)


def _file_manifest(root: Path, relative: str) -> dict:
    path = root / relative
    return {
        "target": relative.replace("\\", "/"),
        "source_path": (Path("bootstrap") / "supervisor" / SUPERVISOR_VERSION / relative).as_posix(),
        "sha256": hashlib.sha256(_canonical_payload(path)).hexdigest(),
    }


def _build_supervisor_manifest(runtime_version: str) -> dict:
    source = ROOT / "bootstrap" / "supervisor" / SUPERVISOR_VERSION
    install_rel = [
        "AI_Bridge.bat",
        "OPEN_AI_BRIDGE.bat",
        "README_FIRST.txt",
        "START_AI_BRIDGE.bat",
        "STOP_AI_BRIDGE.bat",
        "supervisor_update_source.py",
        "_System/VERSION.txt",
        "_System/bootstrap.bat",
        "_System/supervisor.py",
    ]
    upgrade_rel = ["_System/supervisor.py", "_System/VERSION.txt"]
    manifest = {
        "schema_version": "1.1",
        "channel": "stable",
        "version": SUPERVISOR_VERSION,
        "min_runtime_version": runtime_version,
        "upgrade_protocol": "runtime_detached_worker_v1",
        "product_repository": PRODUCT_REPOSITORY,
        "product_ref": PRODUCT_REF,
        "files": [_file_manifest(source, item) for item in upgrade_rel],
        "install_files": [_file_manifest(source, item) for item in install_rel],
        "notes": "Standalone product Supervisor. User Bus is transport/state authority only and is never an implicit Runtime update source.",
    }
    _write_json(ROOT / "supervisor-release.json", manifest)
    return manifest


def main() -> None:
    version = _runtime_version()
    adapter_version = _adapter_version()
    knowledge = _build_clean_knowledge()
    bundle_sha256 = _build_runtime_bundle()
    source_commit = os.environ.get("GITHUB_SHA") or "working-tree"

    runtime_manifest = {
        "schema_version": "1.1",
        "version": version,
        "bundle_path": "runtime_bundle.zip",
        "bundle_sha256": bundle_sha256,
        "runtime_path": "runtime",
        "source_repository": PRODUCT_REPOSITORY,
        "source_ref": PRODUCT_REF,
        "source_commit": source_commit,
        "houdini_adapter_version": adapter_version,
        "min_supervisor_version": SUPERVISOR_VERSION,
        "channel": "stable",
        "knowledge": {
            "mode": "clean_distribution",
            "content_digest": knowledge["content_digest"],
            "manifest_path": "knowledge/houdini/distribution_manifest.json",
            "included_recipes": knowledge["included_recipes"],
            "excluded_recipes": knowledge["excluded_recipes"],
        },
    }
    _write_json(ROOT / "runtime-release.json", runtime_manifest)
    supervisor_manifest = _build_supervisor_manifest(version)

    release = {
        "schema_version": "1.0",
        "product": "AI Bridge",
        "product_repository": PRODUCT_REPOSITORY,
        "product_ref": PRODUCT_REF,
        "runtime_version": version,
        "houdini_adapter_version": adapter_version,
        "supervisor_version": supervisor_manifest["version"],
        "runtime_manifest": "runtime-release.json",
        "supervisor_manifest": "supervisor-release.json",
        "installer": "INSTALL_AI_BRIDGE.ps1",
        "supported_hosts": ["houdini"],
        "unimplemented_hosts": ["unreal", "blender"],
        "runtime_bundle_sha256": bundle_sha256,
        "knowledge_digest": knowledge["content_digest"],
    }
    _write_json(ROOT / "release-manifest.json", release)
    print(
        json.dumps(
            {
                "runtime_version": version,
                "adapter_version": adapter_version,
                "supervisor_version": supervisor_manifest["version"],
                "bundle_sha256": bundle_sha256,
                "knowledge_digest": knowledge["content_digest"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
