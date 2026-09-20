from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

IGNORED_DIRS = {"Binaries", "Intermediate", ".git", "__pycache__"}
IGNORED_FILES = {".ai_bridge_source.json"}


def _iter_files(root: Path):
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if rel.name in IGNORED_FILES or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        yield path, rel


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path, rel in _iter_files(root):
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _plugin_version(root: Path) -> str | None:
    descriptor = Path(root) / "AIBridgeUE.uplugin"
    if not descriptor.is_file():
        return None
    try:
        data = json.loads(descriptor.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = data.get("VersionName")
    return str(value).strip() if value is not None else None


def _assert_unreal_closed(project_file: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("UNREAL_PLUGIN_INSTALL_WINDOWS_ONLY")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("POWERSHELL_NOT_FOUND")
    script = r"""
$ErrorActionPreference='Stop'
$project=[IO.Path]::GetFullPath($args[0]).Replace('/','\')
$targets=@(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @('UnrealEditor.exe','UnrealEditor-Cmd.exe') -and
  $_.CommandLine -and
  $_.CommandLine.Replace('/','\').Contains($project)
})
if($targets.Count -gt 0){
  $targets | Select-Object ProcessId,Name | ConvertTo-Json -Compress
  exit 3
}
exit 0
"""
    try:
        run = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script, str(project_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("UNREAL_PROCESS_CHECK_TIMEOUT") from exc
    if run.returncode == 3:
        raise RuntimeError("TARGET_UNREAL_RUNNING")
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "unknown process check failure").strip()
        raise RuntimeError("UNREAL_PROCESS_CHECK_FAILED: " + detail[-1000:])


def install_plugin(
    project_file: str | Path,
    *,
    source: str | Path,
    force_clean: bool = False,
) -> dict:
    project = Path(project_file).expanduser().resolve()
    if not project.is_file() or project.suffix.lower() != ".uproject":
        raise FileNotFoundError(f"UNREAL_PROJECT_NOT_FOUND: {project}")

    source = Path(source).expanduser().resolve()
    if not (source / "AIBridgeUE.uplugin").is_file():
        raise FileNotFoundError(f"AIBRIDGEUE_SOURCE_INVALID: {source}")

    _assert_unreal_closed(project)

    plugins_dir = project.parent / "Plugins"
    target = plugins_dir / "AIBridgeUE"
    bundled_version = _plugin_version(source)
    source_hash = _source_hash(source)

    installed_version = _plugin_version(target) if target.exists() else None
    installed_hash = _source_hash(target) if target.exists() else None
    if target.exists() and installed_hash == source_hash and not force_clean:
        return {
            "project_file": str(project),
            "install_path": str(target),
            "changed": False,
            "installed": True,
            "installed_version": installed_version,
            "bundled_version": bundled_version,
            "source_hash": source_hash,
            "cleaned_build_artifacts": False,
        }

    plugins_dir.mkdir(parents=True, exist_ok=True)
    staging = plugins_dir / f".AIBridgeUE.ai_bridge_staging_{uuid.uuid4().hex[:10]}"
    backup = plugins_dir / f".AIBridgeUE.ai_bridge_backup_{uuid.uuid4().hex[:10]}"
    if staging.exists() or backup.exists():
        raise RuntimeError("UNREAL_PLUGIN_STAGING_COLLISION")

    def ignore(_directory: str, names: list[str]):
        return [name for name in names if name in IGNORED_DIRS or name in IGNORED_FILES]

    swapped = False
    try:
        shutil.copytree(source, staging, ignore=ignore)
        marker = {
            "schema_version": "1.0",
            "source_hash": source_hash,
            "version": bundled_version,
        }
        (staging / ".ai_bridge_source.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if target.exists():
            target.replace(backup)
        staging.replace(target)
        swapped = True
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if swapped and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if backup.exists() and not target.exists():
            backup.replace(target)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "project_file": str(project),
        "install_path": str(target),
        "changed": True,
        "installed": True,
        "installed_version": _plugin_version(target),
        "bundled_version": bundled_version,
        "source_hash": source_hash,
        "cleaned_build_artifacts": True,
    }
