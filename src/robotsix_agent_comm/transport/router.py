"""Recipient routing with retry + timeout.

The :class:`Router` ties the registry, transport client, and retry policy
together. It resolves ``message.metadata.recipient`` to an endpoint via the
registry (ADR 0003 addressing) and delivers it through the transport
abstraction with bounded retries (ADR 0004 leaves the baseline no-retry;
this network transport layers retries behind the same interface).
"""

from __future__ import annotations

from ..protocol import Message
from .base import Transport
from .brokered import BrokeredRegistry
from .errors import AgentNotFoundError
from .registry import Registry
from .retry import RetryPolicy, retry_call


class Router:
    """Resolves recipients and delivers messages with retry + timeout."""

    def __init__(
        self,
        registry: Registry | BrokeredRegistry,
        client: Transport,
        retry_policy: RetryPolicy,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._registry = registry
        self._client = client
        self._retry_policy = retry_policy
        self._timeout = timeout

    def route(
        self, message: Message, *, timeout: float | None = None
    ) -> Message | None:
        """Deliver ``message`` to its registered recipient.

        Raises:
            AgentNotFoundError: if ``recipient`` is missing or unregistered.
            DeliveryError: if delivery fails after the retry policy is
                exhausted.
        """
        recipient = message.metadata.recipient
        if not recipient:
            raise AgentNotFoundError("message has no recipient")
        endpoint = self._registry.lookup(recipient)
        effective_timeout = self._timeout if timeout is None else timeout
        return retry_call(
            lambda: self._client.send(message, endpoint, timeout=effective_timeout),
            self._retry_policy,
        )
