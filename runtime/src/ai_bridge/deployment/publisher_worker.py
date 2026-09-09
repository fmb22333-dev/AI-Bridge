from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

from ai_bridge.adapters import bridge_admin
from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor


class PublisherAuthorityError(RuntimeError):
    pass


def _assert_source_authority(staging: Path, module_file: str | Path) -> dict:
    staging = Path(staging).resolve()
    source_root = (staging / "src").resolve()
    module_path = Path(module_file).resolve()
    try:
        module_path.relative_to(source_root)
    except ValueError as exc:
        raise PublisherAuthorityError(
            f"publisher module is not loaded from Staging: {module_path}"
        ) from exc
    return {
        "mode": "staging_isolated_subprocess",
        "staging": str(staging),
        "source_root": str(source_root),
        "module_file": str(module_path),
    }


def run(
    request: dict,
    *,
    executor_factory: Callable[..., BridgeAdminExecutor] = BridgeAdminExecutor,
    module_file: str | Path | None = None,
) -> dict:
    if not isinstance(request, dict):
        raise ValueError("publisher request must be an object")

    root = Path(str(request.get("root") or "")).resolve()
    data_dir = Path(str(request.get("data_dir") or "")).resolve()
    staging = Path(str(request.get("staging") or "")).resolve()
    version = str(request.get("version") or "").strip()
    notes = str(request.get("notes") or "")
    if not version:
        raise ValueError("publisher version is required")
    if not staging.is_dir():
        raise FileNotFoundError(staging)

    authority = _assert_source_authority(
        staging,
        module_file or bridge_admin.__file__,
    )
    os.environ["AI_BRIDGE_ROOT"] = str(root)
    executor = executor_factory(data_dir=data_dir)
    if executor.staging.resolve() != staging:
        raise PublisherAuthorityError(
            f"executor staging mismatch: {executor.staging} != {staging}"
        )
    source_version = executor._version(staging)
    if source_version != version:
        raise PublisherAuthorityError(
            f"publisher source version mismatch: expected {version}, got {source_version}"
        )

    payload = executor._publish_artifacts_in_process(version, notes)
    payload = dict(payload)
    payload["publisher_authority"] = {
        **authority,
        "source_version": source_version,
        "executor_module": str(Path(bridge_admin.__file__).resolve()),
    }
    return payload


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        payload = run(request)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
