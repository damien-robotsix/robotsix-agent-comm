"""Broker server for agent registration, discovery, and message routing.

The :class:`BrokerServer` is a standalone HTTP+JSON daemon that lets agents
in separate processes register themselves, discover peers, and exchange
messages without hard-coded endpoint coordinates — implementing the
architecture defined by ADR 0006.

:class:`BrokerConfig` parses ``ROBOTSIX_BROKER_*`` environment variables
and validates the security model.  :func:`build_broker` constructs a
configured :class:`BrokerServer` from a config.
"""

from __future__ import annotations

from .config import BrokerConfig
from .server import BrokerServer
from .service import build_broker

__all__ = ["BrokerConfig", "BrokerServer", "build_broker"]
