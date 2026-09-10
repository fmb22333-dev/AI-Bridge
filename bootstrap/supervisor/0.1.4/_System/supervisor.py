from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from ctypes import wintypes
from pathlib import Path

SUPERVISOR_VERSION = "0.1.4"
SUPERVISOR_UPDATE_PROTOCOL = "runtime_detached_worker_v1"
ROOT = Path(__file__).resolve().parent.parent
SYSTEM_DIR = ROOT / "_System"
RUNTIME_DIR = ROOT / "Runtime"
CURRENT_DIR = RUNTIME_DIR / "Current"
PREVIOUS_DIR = RUNTIME_DIR / "Previous"
STAGING_DIR = RUNTIME_DIR / "Staging"
VERSIONS_DIR = RUNTIME_DIR / "Versions"
VENV_DIR = SYSTEM_DIR / ".venv"
PYTHON = VENV_DIR / "Scripts" / "python.exe" if os.name == "nt" else VENV_DIR / "bin" / "python"


def data_dir() -> Path:
    override = os.environ.get("AI_BRIDGE_DATA_DIR")
    return Path(override).expanduser() if override else Path.home() / ".ai_bridge"


DATA_DIR = data_dir()
SUPERVISOR_PID = DATA_DIR / "supervisor.pid"
SUPERVISOR_STATUS = DATA_DIR / "supervisor_status.json"
SUPERVISOR_STOP = DATA_DIR / "supervisor.stop"
UPDATE_CONFIG = DATA_DIR / "update_source.json"
UPDATE_CHECK = DATA_DIR / "supervisor.update_check"
CONNECTION = DATA_DIR / "connection.json"
ACTIVE_RUNTIME_STATE = DATA_DIR / "active_runtime.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _active_runtime() -> Path:
    try:
        data = json.loads(ACTIVE_RUNTIME_STATE.read_text(encoding="utf-8"))
        candidate = Path(str(data.get("path") or "")).resolve()
        candidate.relative_to(RUNTIME_DIR.resolve())
        if candidate.is_dir():
            return candidate
    except Exception:
        pass
    return CURRENT_DIR.resolve()


def _set_active_runtime(runtime: Path, *, previous: Path | None = None) -> None:
    runtime = runtime.resolve()
    runtime.relative_to(RUNTIME_DIR.resolve())
    payload = {
        "path": str(runtime),
        "version": _runtime_version(runtime),
        "previous": str(previous.resolve()) if previous is not None else None,
        "updated_at": time.time(),
    }
    _write_json(ACTIVE_RUNTIME_STATE, payload)


def _status(**updates) -> dict:
    base = {}
    try:
        if SUPERVISOR_STATUS.exists():
            old = json.loads(SUPERVISOR_STATUS.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                base.update(old)
    except Exception:
        pass
    active = _active_runtime()
    base.update({
        "supervisor_version": SUPERVISOR_VERSION,
        "pid": os.getpid(),
        "root": str(ROOT),
        "runtime_current": str(active),
        "updated_at": time.time(),
    })
    base.update(updates)
    base["updated_at"] = time.time()
    _write_json(SUPERVISOR_STATUS, base)
    return base


def _runtime_version(runtime: Path | None = None) -> str:
    runtime = runtime or _active_runtime()
    path = runtime / "pyproject.toml"
    if not path.exists():
        return "unknown"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"\'')
    return "unknown"


def _read_connection() -> tuple[str, str] | None:
    try:
        data = json.loads(CONNECTION.read_text(encoding="utf-8"))
        return str(data["url"]).rstrip("/"), str(data["token"])
    except Exception:
        return None


def _health_ok(timeout: float = 1.0) -> bool:
    conn = _read_connection()
    if not conn:
        return False
    url, token = conn
    req = urllib.request.Request(url + "/health", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _open_control() -> bool:
    conn = _read_connection()
    if not conn:
        return False
    url, token = conn
    target = "/setup" if not (DATA_DIR / "remote.json").exists() or (DATA_DIR / "remote.disabled").exists() else "/"
    webbrowser.open(f"{url}/control/bootstrap?token={token}&target={target}")
    return True


def _sync_houdini_adapter(runtime: Path) -> None:
    script = runtime / "install_houdini_adapter.py"
    if not script.exists():
        raise RuntimeError("Runtime is missing install_houdini_adapter.py")
    result = subprocess.run([str(PYTHON), str(script)], cwd=str(runtime))
    if result.returncode != 0:
        raise RuntimeError(f"Houdini adapter sync failed ({result.returncode})")


def _ensure_runtime_dependencies(runtime: Path) -> None:
    import tomllib
    pyproject = runtime / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project") or {}
    dependencies = list(project.get("dependencies") or [])
    optional = project.get("optional-dependencies") or {}
    dependencies.extend(optional.get("test") or [])
    if not dependencies:
        return
    command = [str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check", *dependencies]
    result = subprocess.run(command, cwd=str(runtime))
    if result.returncode == 0:
        return
    clean_env = dict(os.environ)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        clean_env.pop(name, None)
    result = subprocess.run(command, cwd=str(runtime), env=clean_env)
    if result.returncode != 0:
        raise RuntimeError("Runtime dependency installation failed")


def _run_validation(runtime: Path) -> None:
    required = [runtime / "pyproject.toml", runtime / "launch_bridge.py", runtime / "src" / "ai_bridge"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError("Invalid runtime payload; missing: " + ", ".join(missing))
    compile_targets = [runtime / "src"]
    if (runtime / "houdini_adapter").exists():
        compile_targets.append(runtime / "houdini_adapter")
    result = subprocess.run(
        [str(PYTHON), "-m", "compileall", "-q", *[str(p) for p in compile_targets]],
        cwd=str(runtime),
    )
    if result.returncode != 0:
        raise RuntimeError("Python compile validation failed")
    tests = runtime / "tests"
    if tests.exists():
        result = subprocess.run(
            [str(PYTHON), "-m", "pytest", "-q", str(tests)],
            cwd=str(runtime),
            env={**os.environ, "PYTHONPATH": str(runtime / "src")},
        )
        if result.returncode != 0:
            raise RuntimeError("Runtime tests failed")


def _runtime_env(runtime: Path) -> dict[str, str]:
    env = dict(os.environ)
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(runtime / "src") + (os.pathsep + old if old else "")
    env["AI_BRIDGE_SUPERVISED"] = "1"
    env["AI_BRIDGE_SUPERVISOR_STATUS"] = str(SUPERVISOR_STATUS)
    env["AI_BRIDGE_ROOT"] = str(ROOT)
    env["AI_BRIDGE_RUNTIME_DIR"] = str(runtime)
    env["AI_BRIDGE_AUTO_OPEN_UI"] = "0"
    return env


def _start_runtime(runtime: Path | None = None) -> subprocess.Popen:
    runtime = (runtime or _active_runtime()).resolve()
    _sync_houdini_adapter(runtime)
    cmd = [str(PYTHON), str(runtime / "launch_bridge.py")]
    kwargs = {"cwd": str(runtime), "env": _runtime_env(runtime)}
    proc = subprocess.Popen(cmd, **kwargs)
    _status(state="runtime_starting", runtime_pid=proc.pid, runtime_version=_runtime_version(runtime))
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Runtime exited during startup ({proc.returncode})")
        if _health_ok(timeout=0.8):
            _status(state="running", runtime_pid=proc.pid, runtime_version=_runtime_version(runtime), last_error=None)
            return proc
        time.sleep(0.35)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise RuntimeError("Runtime health check timed out")


def _stop_runtime(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _load_update_config() -> dict | None:
    # Product Runtime authority is independent from the per-install GitHub Bus.
    # Explicit mirrors are allowed, but legacy Bus bootstrap/publication is not.
    try:
        data = json.loads(UPDATE_CONFIG.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("repository") or "").strip():
            config = dict(data)
            config.setdefault("branch", "main")
            config.setdefault("manifest_path", "runtime-release.json")
            config.setdefault("auto_update", True)
            config.setdefault("check_interval_seconds", 30.0)
            config["bootstrap_from_bus"] = False
            return config
    except Exception:
        pass

    return {
        "repository": "fmb22333-dev/AI-Bridge",
        "branch": "main",
        "manifest_path": "runtime-release.json",
        "auto_update": True,
        "check_interval_seconds": 30.0,
        "bootstrap_from_bus": False,
    }


def _update_token(config: dict) -> str | None:
    # A dedicated source token may be configured later; fall back to the existing bus token.
    source = _dpapi_read(DATA_DIR / "secrets" / "github_source.dpapi")
    if source:
        return source
    return _dpapi_read(DATA_DIR / "secrets" / "github_bus.dpapi")


def _github_headers(token: str | None, *, etag: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AI-Bridge-Supervisor/" + SUPERVISOR_VERSION,
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if etag:
        headers["If-None-Match"] = etag
    return headers


def _github_request(
    url: str,
    token: str | None,
    *,
    method: str = "GET",
    payload: dict | None = None,
    etag: str | None = None,
    allow_404: bool = False,
) -> tuple[int, dict | list | None, dict[str, str]]:
    body = None
    headers = _github_headers(token, etag=etag)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8")) if raw else None
            return resp.status, data, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, dict(exc.headers.items())
        if exc.code == 404 and allow_404:
            return 404, None, dict(exc.headers.items())
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub HTTP {exc.code}: {detail}") from exc


def _github_json(url: str, token: str | None, *, etag: str | None = None) -> tuple[int, dict | list | None, dict[str, str]]:
    return _github_request(url, token, etag=etag)


def _contents_url(repository: str, path: str) -> str:
    from urllib.parse import quote
    encoded = "/".join(quote(part, safe="") for part in path.strip("/").split("/"))
    return f"https://api.github.com/repos/{repository}/contents/{encoded}"


def _put_content(repository: str, branch: str, path: str, raw: bytes, token: str | None, message: str) -> None:
    from urllib.parse import quote
    url = _contents_url(repository, path)
    status, existing, _ = _github_request(
        url + "?ref=" + quote(branch, safe=""), token, allow_404=True
    )
    payload = {
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "branch": branch,
    }
    if status == 200 and isinstance(existing, dict) and existing.get("sha"):
        payload["sha"] = existing["sha"]
    _github_request(url, token, method="PUT", payload=payload)


def _ensure_branch(repository: str, branch: str, base_branch: str, token: str | None) -> None:
    from urllib.parse import quote
    branch_url = f"https://api.github.com/repos/{repository}/branches/{quote(branch, safe='')}"
    status, _, _ = _github_request(branch_url, token, allow_404=True)
    if status == 200:
        return
    ref_url = f"https://api.github.com/repos/{repository}/git/ref/heads/{quote(base_branch, safe='')}"
    _, ref, _ = _github_request(ref_url, token)
    try:
        sha = str(ref["object"]["sha"])
    except Exception as exc:
        raise RuntimeError("Unable to resolve base branch for Runtime bootstrap") from exc
    _github_request(
        f"https://api.github.com/repos/{repository}/git/refs",
        token,
        method="POST",
        payload={"ref": f"refs/heads/{branch}", "sha": sha},
    )


def _build_runtime_bundle(runtime: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(runtime.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(runtime)
            if "__pycache__" in rel.parts or ".pytest_cache" in rel.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            zf.write(path, (Path("runtime") / rel).as_posix())


def _bootstrap_release_if_needed(config: dict, token: str | None) -> None:
    # A user's Bus is transport/state authority only. Runtime publication into it
    # is forbidden in the standalone product architecture.
    if config.get("bootstrap_from_bus"):
        raise RuntimeError("BUS_RUNTIME_BOOTSTRAP_FORBIDDEN")


def _download_bundle(repository: str, branch: str, path: str, token: str | None, target: Path) -> None:
    from urllib.parse import quote
    status, payload, _ = _github_request(
        _contents_url(repository, path) + "?ref=" + quote(branch, safe=""), token
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError("Unable to download Runtime bundle")
    content = str(payload.get("content") or "").replace("\n", "")
    if not content:
        raise RuntimeError("Runtime bundle is empty")
    target.write_bytes(base64.b64decode(content))


def _extract_runtime_archive(archive: Path, target: Path, runtime_path: str) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ai_bridge_update_") as temp:
        temp_root = Path(temp)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temp_root)
        payload = temp_root / runtime_path.strip("/\\") if runtime_path not in ("", ".") else temp_root
        if not payload.exists():
            raise RuntimeError(f"Runtime path not found in bundle: {runtime_path}")
        for item in payload.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)


def _manifest(config: dict, token: str | None, etag: str | None) -> tuple[dict | None, str | None, dict[str, str]]:
    repo = str(config["repository"])
    branch = str(config.get("branch") or "main")
    manifest_path = str(config.get("manifest_path") or "runtime-release.json")
    url = f"https://api.github.com/repos/{repo}/contents/{manifest_path}?ref={branch}"
    status, payload, headers = _github_json(url, token, etag=etag)
    if status == 304:
        return None, etag, headers
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid update manifest response")
    content = str(payload.get("content") or "").replace("\n", "")
    if not content:
        raise RuntimeError("Update manifest is empty")
    manifest = json.loads(base64.b64decode(content).decode("utf-8"))
    return manifest, headers.get("ETag") or headers.get("Etag"), headers


def _version_dir(version: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", version).strip("._-")
    if not safe:
        raise RuntimeError("Invalid runtime version")
    return VERSIONS_DIR / safe


def _apply_update(proc: subprocess.Popen | None, config: dict, manifest: dict, token: str | None) -> subprocess.Popen:
    version = str(manifest.get("version") or "").strip()
    runtime_path = str(manifest.get("runtime_path") or "runtime")
    bundle_path = str(manifest.get("bundle_path") or "runtime_bundle.zip")
    if not version:
        raise RuntimeError("Manifest missing version")

    active_runtime = _active_runtime()
    if version == _runtime_version(active_runtime):
        return proc if proc is not None else _start_runtime(active_runtime)

    _status(state="updating", target_version=version, previous_runtime=str(active_runtime))
    archive = SYSTEM_DIR / "update.zip"
    if archive.exists():
        archive.unlink()
    _download_bundle(
        str(config["repository"]),
        str(config.get("branch") or "main"),
        bundle_path,
        token,
        archive,
    )

    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    target_runtime = _version_dir(version)
    candidate = VERSIONS_DIR / (".candidate-" + target_runtime.name)
    if candidate.exists():
        shutil.rmtree(candidate, ignore_errors=True)
    _extract_runtime_archive(archive, candidate, runtime_path)
    _ensure_runtime_dependencies(candidate)
    _run_validation(candidate)

    if target_runtime.exists() and target_runtime.resolve() != active_runtime.resolve():
        shutil.rmtree(target_runtime, ignore_errors=True)
    candidate.replace(target_runtime)

    _stop_runtime(proc)
    proc = None
    try:
        proc = _start_runtime(target_runtime)
        _set_active_runtime(target_runtime, previous=active_runtime)
        _status(
            state="running",
            runtime_version=_runtime_version(target_runtime),
            runtime_current=str(target_runtime),
            previous_runtime=str(active_runtime),
            last_update_version=version,
            last_update_error=None,
            rollback_available=True,
        )
        return proc
    except Exception as exc:
        _status(state="rollback", last_error=f"{type(exc).__name__}: {exc}")
        _stop_runtime(proc)
        proc = _start_runtime(active_runtime)
        _set_active_runtime(active_runtime)
        _status(
            state="running",
            runtime_version=_runtime_version(active_runtime),
            runtime_current=str(active_runtime),
            last_error=f"Update {version} rolled back: {type(exc).__name__}: {exc}",
            last_update_error=f"{type(exc).__name__}: {exc}",
        )
        return proc
    finally:
        try:
            archive.unlink()
        except FileNotFoundError:
            pass
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)


def _monitor() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SUPERVISOR_STOP.unlink()
    except FileNotFoundError:
        pass
    SUPERVISOR_PID.write_text(str(os.getpid()), encoding="utf-8")
    _status(state="starting", runtime_version=_runtime_version(), runtime_current=str(_active_runtime()))

    proc: subprocess.Popen | None = None
    etag: str | None = None
    next_update_check = 0.0
    restart_times: list[float] = []
    try:
        proc = _start_runtime()
        # Only the human-initiated Supervisor startup opens the dashboard.
        # Runtime restarts/updates are intentionally silent to avoid duplicate tabs.
        _open_control()
        while True:
            if SUPERVISOR_STOP.exists():
                _status(state="stopping")
                break
            if proc.poll() is not None:
                now = time.monotonic()
                restart_times = [t for t in restart_times if now - t < 60]
                restart_times.append(now)
                if len(restart_times) > 3:
                    _status(state="failed", runtime_pid=None, last_error="Runtime crashed repeatedly; automatic restart stopped")
                    return 2
                _status(state="runtime_restarting", last_error=f"Runtime exited ({proc.returncode}); restarting")
                time.sleep(1.0)
                proc = _start_runtime()

            config = _load_update_config()
            now = time.monotonic()
            if UPDATE_CHECK.exists():
                try:
                    UPDATE_CHECK.unlink()
                except OSError:
                    pass
                next_update_check = 0.0
            if config and bool(config.get("auto_update", True)) and now >= next_update_check:
                interval = max(15.0, float(config.get("check_interval_seconds", 30.0)))
                next_update_check = now + interval
                try:
                    token = _update_token(config)
                    _bootstrap_release_if_needed(config, token)
                    config = _load_update_config() or config
                    manifest, etag, headers = _manifest(config, token, etag)
                    rate = {
                        "remaining": headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining"),
                        "limit": headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit"),
                        "reset": headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset"),
                    }
                    _status(update_source=config, update_rate_limit=rate, update_check_interval_seconds=interval)
                    if manifest and str(manifest.get("version")) != _runtime_version(_active_runtime()):
                        proc = _apply_update(proc, config, manifest, token)
                except Exception as exc:
                    _status(last_update_error=f"{type(exc).__name__}: {exc}")

            time.sleep(0.25)
    finally:
        _stop_runtime(proc)
        try:
            SUPERVISOR_PID.unlink()
        except FileNotFoundError:
            pass
        try:
            SUPERVISOR_STOP.unlink()
        except FileNotFoundError:
            pass
        _status(state="stopped", runtime_pid=None)
    return 0


def _request_stop() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUPERVISOR_STOP.write_text("stop", encoding="utf-8")
    try:
        pid = int(SUPERVISOR_PID.read_text(encoding="utf-8").strip())
    except Exception:
        print("AI Bridge Supervisor is not running.")
        return 0
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if not SUPERVISOR_PID.exists():
            print("AI Bridge stopped.")
            return 0
        time.sleep(0.2)
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    print("AI Bridge stop requested.")
    return 0


def _print_status() -> int:
    try:
        state = json.loads(SUPERVISOR_STATUS.read_text(encoding="utf-8"))
    except Exception:
        state = {"state": "unknown", "runtime_version": _runtime_version()}
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Bridge stable supervisor")
    parser.add_argument("command", choices=["run", "stop", "open", "status"], nargs="?", default="run")
    args = parser.parse_args()
    if args.command == "run":
        return _monitor()
    if args.command == "stop":
        return _request_stop()
    if args.command == "open":
        if _open_control():
            return 0
        print("AI Bridge is not running yet.")
        return 1
    return _print_status()


if __name__ == "__main__":
    raise SystemExit(main())
