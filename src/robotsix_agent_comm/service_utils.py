"""Shared utilities for service entrypoints.

Provides :func:`_run_until_signalled` — a reusable signal-handling /
wait / stop loop used by both the broker and lifecycle server
entrypoints.
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any


def _run_until_signalled(server: Any, logger: logging.Logger) -> None:
    """Register SIGTERM/SIGINT handlers, block, then ``server.stop()``.

    *server* must have a ``.stop() -> None`` method.
    """
    shutdown_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    shutdown_event.wait()
    server.stop()
