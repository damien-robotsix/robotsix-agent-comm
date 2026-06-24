"""Convenience function for broker-level agent discovery."""

from __future__ import annotations

import ssl

from ..transport.brokered import BrokeredRegistry
from ..transport.endpoints import AgentInfo


def discover_agents(
    *,
    broker_host: str,
    broker_port: int = 443,
    broker_token: str | None = None,
    broker_scheme: str = "https",
    tls_ca: str | None = None,
    ssl_context: ssl.SSLContext | None = None,
    timeout: float = 30.0,
) -> list[AgentInfo]:
    """Return all agents registered on the broker with their capabilities.

    Uses the existing bearer-token auth scheme (``broker_token``).
    """
    if ssl_context is None and tls_ca:
        ssl_context = ssl.create_default_context(cafile=tls_ca)

    registry = BrokeredRegistry(
        broker_host,
        broker_port,
        scheme=broker_scheme,
        ssl_context=ssl_context,
        agent_token=broker_token,
    )
    return registry.discover_agents()
