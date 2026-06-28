"""Shared logging configuration for CLI entry points."""

from __future__ import annotations

import logging
import os
import sys

_LOG_LEVEL_STR = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_STR, None)
if not isinstance(_LOG_LEVEL, int):
    _LOG_LEVEL = logging.INFO

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(*, force: bool = True) -> None:
    """Configure the root logger with a basic stdout handler.

    Must be called **as the first statement** in ``main()``, before
    any module-level ``logger`` calls or framework initialization
    that may trigger lazy logging from dependencies.

    Reads ``LOG_LEVEL`` from the environment (default ``INFO``).
    Unknown/typo values are silently downgraded to ``INFO``.
    """
    logging.basicConfig(
        level=_LOG_LEVEL,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        stream=sys.stdout,
        force=force,
    )
