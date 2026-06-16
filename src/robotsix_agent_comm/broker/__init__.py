"""Broker server for agent registration, discovery, and message routing.

The :class:`BrokerServer` is a standalone HTTP+JSON daemon that lets agents
in separate processes register themselves, discover peers, and exchange
messages without hard-coded endpoint coordinates — implementing the
architecture defined by ADR 0006.
"""

from __future__ import annotations

from .server import BrokerServer

__all__ = ["BrokerServer"]
