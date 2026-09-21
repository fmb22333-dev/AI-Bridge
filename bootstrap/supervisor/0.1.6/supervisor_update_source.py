from __future__ import annotations

from typing import Any

PRODUCT_REPOSITORY = "fmb22333-dev/AI-Bridge"
PRODUCT_REF = "main"
DEFAULT_MANIFEST_PATH = "runtime-release.json"
DEFAULT_CHECK_INTERVAL_SECONDS = 30.0


def resolve_update_source(
    *,
    explicit: dict[str, Any] | None,
    remote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the Supervisor Runtime update source.

    Product updates come from the shared AI-Bridge product repository by default.
    A per-install Bus repository is intentionally ignored here; it remains a
    transport/state repository and must never become an implicit Runtime source.
    """

    if isinstance(explicit, dict) and str(explicit.get("repository") or "").strip():
        config = dict(explicit)
        config.setdefault("branch", PRODUCT_REF)
        config.setdefault("manifest_path", DEFAULT_MANIFEST_PATH)
        config.setdefault("auto_update", True)
        config.setdefault("check_interval_seconds", DEFAULT_CHECK_INTERVAL_SECONDS)
        # Legacy Bus-bootstrap behavior is forbidden in the product model.
        config["bootstrap_from_bus"] = False
        return config

    return {
        "repository": PRODUCT_REPOSITORY,
        "branch": PRODUCT_REF,
        "manifest_path": DEFAULT_MANIFEST_PATH,
        "auto_update": True,
        "check_interval_seconds": DEFAULT_CHECK_INTERVAL_SECONDS,
        "bootstrap_from_bus": False,
    }
