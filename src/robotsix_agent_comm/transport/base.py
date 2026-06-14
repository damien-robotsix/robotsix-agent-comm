"""Abstract transport interface.

ADR 0002 defines an abstract transport whose sole responsibility is to
move serialized :class:`~robotsix_agent_comm.protocol.Message` envelopes
between endpoints; it does not resolve addresses (the router's job) or
interpret bodies. The broker/router and client API program to this
abstraction, so alternative transports (the in-process baseline, this
HTTP+JSON network transport, or a future durable one) are swappable
without changing message semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..protocol import Message
from .endpoints import Endpoint


class Transport(ABC):
    """Moves serialized messages between endpoints."""

    @abstractmethod
    def send(
        self, message: Message, endpoint: Endpoint, *, timeout: float
    ) -> Message | None:
        """Deliver ``message`` to ``endpoint`` and return any reply.

        Returns the correlated reply for request/response exchanges, or
        ``None`` for fire-and-forget messages.
        """

    @abstractmethod
    def health_check(self, endpoint: Endpoint, *, timeout: float) -> bool:
        """Return ``True`` if ``endpoint`` is reachable and healthy."""
