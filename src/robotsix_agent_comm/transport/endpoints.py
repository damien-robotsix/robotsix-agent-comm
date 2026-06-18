"""Endpoint descriptor for the HTTP+JSON transport.

An :class:`Endpoint` names where an agent listens. Endpoints are kept
URL-shaped so a future relay/gateway can address NAT/firewall traversal
without changing this layer (that traversal is out of scope today).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Default route on which an agent receives messages.
DEFAULT_MESSAGE_PATH = "/messages"

#: Route serving liveness checks.
HEALTH_PATH = "/health"


@dataclass(kw_only=True)
class Endpoint:
    """Where an agent listens for transport messages.

    Mirrors the ``kw_only`` dataclass style of ``protocol.messages``.
    """

    agent_id: str
    host: str
    port: int
    scheme: str = "http"
    path: str = DEFAULT_MESSAGE_PATH
    #: When True the agent has no dialable listener; the broker holds its
    #: messages in a mailbox and the agent fetches them via ``GET /messages``
    #: (NAT-safe pull delivery). host/port are placeholders in this case.
    mailbox: bool = False

    @property
    def base_url(self) -> str:
        """Return the ``scheme://host:port`` origin of this endpoint."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        """Return the full URL of this endpoint's message-receive route."""
        return f"{self.base_url}{self.path}"

    @property
    def health_url(self) -> str:
        """Return the full URL of this endpoint's health route."""
        return f"{self.base_url}{HEALTH_PATH}"
