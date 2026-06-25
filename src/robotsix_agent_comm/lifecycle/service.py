"""Lifecycle builder and service entrypoint.

Provides :func:`build_lifecycle` (constructs a :class:`LifecycleServer`
from a :class:`LifecycleConfig`) and :func:`main` (CLI entrypoint that
reads env vars, builds, starts, and blocks until signalled).
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any

from .backend import SubprocessBackend
from .config import LifecycleConfig
from .server import LifecycleServer

logger = logging.getLogger(__name__)


def build_lifecycle(config: LifecycleConfig) -> LifecycleServer:
    """Build a :class:`LifecycleServer` from a validated *config*."""
    backend = SubprocessBackend()

    kwargs: dict[str, Any] = {
        "backend": backend,
        "host": config.host,
        "port": config.port,
        "auth_token": config.auth_token,
        "health_timeout_seconds": config.health_timeout_seconds,
        "health_interval_seconds": config.health_interval_seconds,
        "health_check_enabled": config.health_check_enabled,
    }

    return LifecycleServer(**kwargs)


def main(argv: list[str] | None = None) -> int:
    """Parse env, build lifecycle server, start it, and block until signalled.

    Returns ``0`` on clean shutdown, non-zero on configuration errors.
    """
    # -- Parse config --------------------------------------------------
    try:
        config = LifecycleConfig.from_env()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    # -- Build and start -----------------------------------------------
    server = build_lifecycle(config)
    server.start()
    logger.info(
        "Lifecycle server listening on %s:%s",
        server.host,
        server.port,
    )

    # -- Signal handling -----------------------------------------------
    shutdown_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # -- Block until signalled -----------------------------------------
    shutdown_event.wait()
    server.stop()
    logger.info("Lifecycle server stopped.")
    return 0
