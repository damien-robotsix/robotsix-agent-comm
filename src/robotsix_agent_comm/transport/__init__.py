"""Agent-communication network transport layer.

HTTP+JSON-over-stdlib transport (ADR 0005) implemented behind the abstract
transport interface defined by ADR 0002: an :class:`Endpoint` descriptor, an
in-memory :class:`Registry`, a :class:`TransportServer`/:class:`TransportClient`
wire pair, a :class:`RetryPolicy` with exponential backoff, and a
:class:`Router` that resolves recipients and delivers with retry + timeout.

The layer is stdlib-only and consumes the Phase-4 ``protocol`` package as-is.
NAT/firewall/relay traversal is out of scope; endpoints are kept URL-shaped so
a relay/gateway can be added later (see ``docs/design/transport.md``).
"""

from __future__ import annotations

from .base import Transport
from .client import TransportClient
from .endpoints import Endpoint
from .errors import (
    AgentNotFoundError,
    DeliveryError,
    TransportError,
    TransportTimeoutError,
)
from .registry import Registry
from .retry import RetryPolicy, retry_call
from .router import Router
from .server import TransportServer

__all__ = [
    "AgentNotFoundError",
    "DeliveryError",
    "Endpoint",
    "Registry",
    "RetryPolicy",
    "Router",
    "Transport",
    "TransportClient",
    "TransportError",
    "TransportServer",
    "TransportTimeoutError",
    "retry_call",
]
