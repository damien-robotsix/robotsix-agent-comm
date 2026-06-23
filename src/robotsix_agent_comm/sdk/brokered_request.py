"""One-shot brokered request helper.

:class:`BrokeredRequester` encapsulates the repeated *per-call* boilerplate
that consumers otherwise open-code on top of the lower-level
``create_transport_pair`` + ``Agent`` primitives.  Unlike the long-lived
:class:`~.brokered.BrokeredAgent`, this class is request-scoped: each
:meth:`~BrokeredRequester.request` call stands up a transport pair, issues a
single request, unwraps the reply, and tears down.
"""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from typing import Any

from ..protocol import Error
from ..transport.brokered import create_transport_pair
from .agent import Agent
from .reply import reply_text

__all__ = ["BrokeredRequester"]


class BrokeredRequester:
    """One-shot helper for brokered request/response interactions.

    Constructor kwargs mirror the connection-arg surface of
    :class:`BrokeredAgent` for consistency.

    Args:
        agent_id: This requester's agent id on the broker.
        target_agent_id: The agent id of the responder being called.
        broker_host: Broker hostname (e.g. ``ai-broker.robotsix.net``).
        broker_token: This agent's bearer token (``None`` only for an
            auth-disabled broker, e.g. in tests).
        broker_port: Broker port (default 443).
        broker_scheme: ``"https"`` (default) or ``"http"``.
        broker_ssl_context: Optional explicit :class:`ssl.SSLContext`.
        timeout: Per-request timeout in seconds.
        default_reply: Fallback string returned when the response body
            does not contain a ``"reply"`` key.
    """

    def __init__(
        self,
        agent_id: str,
        target_agent_id: str,
        *,
        broker_host: str,
        broker_token: str | None,
        broker_port: int = 443,
        broker_scheme: str = "https",
        broker_ssl_context: ssl.SSLContext | None = None,
        timeout: float = 30.0,
        default_reply: str = "",
    ) -> None:
        """Initialize the brokered request with broker connection settings."""
        self.agent_id = agent_id
        self.target_agent_id = target_agent_id
        self._broker_host = broker_host
        self._broker_token = broker_token
        self._broker_port = broker_port
        self._broker_scheme = broker_scheme
        self._broker_ssl_context = broker_ssl_context
        self._timeout = timeout
        self._default_reply = default_reply

    def request(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        default: str | None = None,
    ) -> str:
        """Issue a request and return the extracted reply string.

        Parameters:
            payload: Optional request body (a mapping).
            timeout: Per-call timeout in seconds; when ``None`` the
                instance-level *timeout* is used.
            default: Per-call fallback string; when ``None`` the
                instance-level *default_reply* is used.

        Returns:
            The ``"reply"`` value from the response body, or the configured
            fallback when no reply is present.

        Raises:
            RuntimeError: When the broker returns an ``Error`` message
                (the message includes the target agent id and the error's
                ``"message"`` field).
        """
        effective_timeout = timeout if timeout is not None else self._timeout
        effective_default = default if default is not None else self._default_reply

        registry, transport = create_transport_pair(
            "brokered",
            broker_host=self._broker_host,
            broker_port=self._broker_port,
            broker_scheme=self._broker_scheme,
            broker_ssl_context=self._broker_ssl_context,
            broker_token=self._broker_token,
        )

        agent = Agent(
            self.agent_id,
            registry,
            transport=transport,
            pull=True,
            timeout=effective_timeout,
        )

        with agent:
            reply = agent.send_request(
                self.target_agent_id,
                dict(payload) if payload is not None else None,
                timeout=effective_timeout,
            )

        if isinstance(reply, Error):
            msg = reply.body.get("message") if isinstance(reply.body, dict) else None
            raise RuntimeError(
                f"brokered request to {self.target_agent_id!r} failed: {msg}"
            )

        body = getattr(reply, "body", None)
        return reply_text(body, default=effective_default)
