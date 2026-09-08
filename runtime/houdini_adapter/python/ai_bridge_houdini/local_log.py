from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER = logging.getLogger("ai_bridge_houdini")
if not _LOGGER.handlers:
    log_dir = Path.home() / ".ai_bridge"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "houdini_adapter.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def info(message: str) -> None:
    _LOGGER.info(message)


def exception(message: str) -> None:
    _LOGGER.exception(message)
