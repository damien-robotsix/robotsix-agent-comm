"""Lifecycle server builder and service entrypoint.

Provides :func:`build_server` (constructs a :class:`LifecycleServer` from a
:class:`LifecycleConfig`) and :func:`main` (CLI entrypoint that reads env
vars, builds, starts, and blocks until signalled).
"""

from __future__ import annotations

import logging

from ..service_utils import _run_until_signalled
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
    from robotsix_agent_comm._logging import setup_logging

    setup_logging()

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

    # -- Block until signalled -----------------------------------------
    _run_until_signalled(server, logger)
    logger.info("Lifecycle server stopped.")
    return 0
