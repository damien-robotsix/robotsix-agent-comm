"""Broker builder and service entrypoint.

Provides :func:`build_broker` (constructs a :class:`BrokerServer` from a
:class:`BrokerConfig`) and :func:`main` (CLI entrypoint that reads env
vars, builds, starts, and blocks until signalled).
"""

from __future__ import annotations

import logging
import signal
import ssl
import threading
from typing import Any

from .config import BrokerConfig
from .server import BrokerServer

logger = logging.getLogger(__name__)


def build_broker(config: BrokerConfig) -> BrokerServer:
    """Build a :class:`BrokerServer` from a validated *config*.

    Constructs an :class:`ssl.SSLContext` when TLS material is present,
    mirrors the ``ssl`` construction pattern used in
    ``tests/test_end_to_end.py`` (``_write_certs_to_dir`` /
    ``load_cert_chain`` / ``load_verify_locations``).
    """
    ssl_context: ssl.SSLContext | None = None

    # -- TLS ---------------------------------------------------------
    if config.tls_cert and config.tls_key:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(config.tls_cert, config.tls_key)
        if config.tls_ca:
            ssl_context.load_verify_locations(config.tls_ca)

    # -- Auth ---------------------------------------------------------
    agent_tokens = config.agent_tokens

    # -- Build keyword arguments for BrokerServer ---------------------
    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "ssl_context": ssl_context,
        "require_client_cert": config.require_client_cert,
        "agent_tokens": agent_tokens,
    }
    if config.ttl_seconds is not None:
        kwargs["ttl_seconds"] = config.ttl_seconds
    if config.rate_limit is not None:
        kwargs["rate_limit_per_second"] = config.rate_limit
    if config.max_body_size is not None:
        kwargs["max_body_size"] = config.max_body_size
    if config.audit_log is not None:
        kwargs["audit_log_path"] = config.audit_log
    if config.mailbox_grace_seconds is not None:
        kwargs["mailbox_grace_seconds"] = config.mailbox_grace_seconds
    kwargs["dashboard_enabled"] = config.dashboard_enabled

    return BrokerServer(**kwargs)


def main(argv: list[str] | None = None) -> int:
    """Parse env, build broker, start it, and block until signalled.

    Returns ``0`` on clean shutdown, non-zero on configuration errors.
    """
    # -- Parse config --------------------------------------------------
    try:
        config = BrokerConfig.from_env()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    # -- Build and start -----------------------------------------------
    broker = build_broker(config)
    broker.start()
    logger.info(
        "Broker listening on %s:%s",
        broker.host,
        broker.port,
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
    broker.stop()
    logger.info("Broker stopped.")
    return 0
