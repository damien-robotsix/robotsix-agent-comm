"""Transport-layer exception hierarchy.

These errors are deliberately distinct from :class:`protocol.ProtocolError`:
they describe failures of *moving* a message (addressing, delivery, timeout),
not failures of the message envelope itself.
"""

from __future__ import annotations

from robotsix_agent_comm.errors import RobotsixAgentCommError


class TransportError(RobotsixAgentCommError):
    """Base class for all transport-level errors."""


class AgentNotFoundError(TransportError):
    """Raised when an ``agent_id`` is not registered with the registry."""


class TransportTimeoutError(TransportError, TimeoutError):
    """Raised when a transport request exceeds its per-request timeout.

    Subclasses the stdlib :class:`TimeoutError` so callers may catch either
    the transport hierarchy or the builtin timeout type.
    """


class DeliveryError(TransportError):
    """Raised when delivery fails after the retry policy is exhausted.

    The last underlying error (if any) is preserved on :attr:`cause` and
    chained via ``raise ... from`` so the original failure is not lost.
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


#: Error code for the ``"delivery_failed"`` error envelope.
DELIVERY_FAILED: str = "delivery_failed"
#: Error code for the ``"unknown_recipient"`` error envelope.
UNKNOWN_RECIPIENT: str = "unknown_recipient"
