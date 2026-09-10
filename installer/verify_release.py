from __future__ import annotations

import hashlib
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    runtime = json.loads((ROOT / "runtime-release.json").read_text(encoding="utf-8"))
    supervisor = json.loads((ROOT / "supervisor-release.json").read_text(encoding="utf-8"))
    release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    version = str(tomllib.loads((RUNTIME / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
    if runtime.get("version") != version or release.get("runtime_version") != version:
        fail("Runtime version authority mismatch")
    bundle = ROOT / str(runtime["bundle_path"])
    if sha256(bundle) != runtime.get("bundle_sha256"):
        fail("Runtime bundle SHA-256 mismatch")
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        required = {
            "runtime/pyproject.toml",
            "runtime/launch_bridge.py",
            "runtime/src/ai_bridge/app.py",
            "runtime/src/ai_bridge/web/routes.py",
            "runtime/src/ai_bridge/web/static/app.css",
            "runtime/src/ai_bridge/web/static/app.js",
        }
        missing = sorted(required - names)
        if missing:
            fail("Runtime bundle missing: " + ", ".join(missing))
    for item in supervisor.get("install_files") or []:
        path = ROOT / item["source_path"]
        if not path.is_file():
            fail(f"Supervisor install file missing: {path}")
        if sha256(path) != item.get("sha256"):
            fail(f"Supervisor install file digest mismatch: {path}")
    routes = (RUNTIME / "src" / "ai_bridge" / "web" / "routes.py").read_text(encoding="utf-8")
    supervisor_py = (ROOT / "bootstrap" / "supervisor" / "0.1.4" / "_System" / "supervisor.py").read_text(encoding="utf-8")
    combined = routes + supervisor_py
    if "fmb22333-dev/ai-bridge-bus" in combined:
        fail("Developer Bus leaked into product update authority")
    if '"branch": "bridge-runtime"' in combined:
        fail("Legacy bridge-runtime branch leaked into product defaults")
    if '"manifest_path": "distribution-release.json"' in combined:
        fail("Legacy distribution-release authority leaked into product defaults")
    sys.path.insert(0, str(RUNTIME / "src"))
    from ai_bridge.deployment.knowledge_pack import validate_distribution
    knowledge = ROOT / "knowledge" / "houdini"
    result = validate_distribution(knowledge)
    if not result["ok"]:
        fail("Clean Knowledge validation failed: " + "; ".join(result["violations"]))
    manifest = json.loads((knowledge / "distribution_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("content_digest") != result["content_digest"]:
        fail("Clean Knowledge manifest digest mismatch")
    print(f"AI Bridge release verification PASS: Runtime {version}, Supervisor {supervisor['version']}")


if __name__ == "__main__":
    main()
