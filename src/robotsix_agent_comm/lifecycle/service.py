"""Lifecycle server builder and service entrypoint.

Provides :func:`build_server` (constructs a :class:`LifecycleServer` from a
:class:`LifecycleConfig`) and :func:`main` (CLI entrypoint that reads env
vars, builds, starts, and blocks until signalled).
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any

from .config import LifecycleConfig
from .server import LifecycleServer
from .tracing import LifecycleTracing

logger = logging.getLogger(__name__)


def build_server(config: LifecycleConfig) -> LifecycleServer:
    """Build a :class:`LifecycleServer` from a validated *config*.

    Constructs a :class:`LifecycleTracing` from the config's Langfuse
    settings (public key, secret key, and host), then creates and
    returns a :class:`LifecycleServer` with that tracing instance.
    """
    tracing = LifecycleTracing(
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        host=config.langfuse_host,
    )
    return LifecycleServer(config=config, tracing=tracing)


def main(argv: list[str] | None = None) -> int:
    """Parse env, build lifecycle server, start it, and block until signalled.

    Returns ``0`` on clean shutdown, ``1`` on configuration errors.
    """
    # -- Parse config --------------------------------------------------
    try:
        config = LifecycleConfig.from_env()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    # -- Build and start -----------------------------------------------
    server = build_server(config)
    server.start()
    logger.info(
        "Lifecycle server started as agent %r",
        server.agent_id,
    )

    # -- Signal handling -----------------------------------------------
    shutdown_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # -- Block until signalled -----------------------------------------
    shutdown_event.wait()
    server.stop()
    logger.info("Lifecycle server stopped.")
    return 0
