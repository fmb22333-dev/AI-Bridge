from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


def default_houdini_user_dir() -> Path:
    override = os.environ.get("AI_BRIDGE_HOUDINI_USER_DIR")
    if override:
        return Path(override).expanduser()

    home = Path.home()
    candidates = [home / "Documents" / "houdini21.0"]
    for onedrive in sorted(home.glob("OneDrive*")):
        candidates.extend([
            onedrive / "Documents" / "houdini21.0",
            onedrive / "文档" / "houdini21.0",
        ])
    candidates.append(home / "houdini21.0")
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    roots = [source / "python", source / "scripts"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    package = source / "package" / "ai_bridge_houdini.json"
    if package.exists():
        files.append(package)
    for path in sorted(files, key=lambda p: p.relative_to(source).as_posix()):
        rel = path.relative_to(source).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def install_adapter(
    houdini_user_dir: Path,
    *,
    source: Path | None = None,
    running_safe: bool = False,
    force_clean: bool = False,
) -> dict:
    user_dir = houdini_user_dir.expanduser().resolve()
    source = (source or (Path(__file__).resolve().parent / "houdini_adapter")).resolve()
    target = user_dir / "ai_bridge_houdini"
    packages = user_dir / "packages"
    marker = target / ".ai_bridge_source.json"
    package_target = packages / "ai_bridge_houdini.json"
    source_hash = _source_hash(source)

    current_hash = None
    if marker.exists() and package_target.exists():
        try:
            current_hash = json.loads(marker.read_text(encoding="utf-8")).get("source_hash")
        except (OSError, ValueError, TypeError):
            current_hash = None

    if current_hash == source_hash and not force_clean:
        return {
            "changed": False,
            "adapter": str(target),
            "package": str(package_target),
            "source_hash": source_hash,
            "running_safe": running_safe,
            "force_clean": force_clean,
        }

    target.mkdir(parents=True, exist_ok=True)
    packages.mkdir(parents=True, exist_ok=True)
    for name in ("python", "scripts"):
        src = source / name
        dst = target / name
        if running_safe and not force_clean:
            shutil.copytree(
                src,
                dst,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    shutil.copy2(source / "package" / "ai_bridge_houdini.json", package_target)
    marker.write_text(
        json.dumps(
            {
                "source_hash": source_hash,
                "running_safe_stage": running_safe,
                "force_clean": force_clean,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "changed": True,
        "adapter": str(target),
        "package": str(package_target),
        "source_hash": source_hash,
        "running_safe": running_safe,
        "force_clean": force_clean,
    }


def main():
    parser = argparse.ArgumentParser(description="Install AI Bridge Houdini Adapter")
    parser.add_argument("--houdini-user-dir", type=Path, default=default_houdini_user_dir())
    parser.add_argument("--running-safe", action="store_true")
    args = parser.parse_args()
    result = install_adapter(args.houdini_user_dir, running_safe=args.running_safe)
    if result["changed"]:
        print("AI Bridge Houdini Adapter installed/updated:")
        print("  Adapter:", result["adapter"])
        print("  Package:", result["package"])
        if args.running_safe:
            print("Adapter files staged safely for the next Houdini process start.")
        else:
            print("Restart Houdini if it is currently open.")
    else:
        print("AI Bridge Houdini Adapter already up to date.")
        print("  Adapter:", result["adapter"])


if __name__ == "__main__":
    main()
