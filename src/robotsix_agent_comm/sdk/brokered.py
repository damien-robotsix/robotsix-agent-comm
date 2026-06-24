"""Batteries-included brokered agent client.

:class:`BrokeredAgent` is the one-call entry point consumers should use to talk
to a secured broker, instead of re-wiring ``create_transport_pair`` + ``Agent``
themselves. It owns the TLS/auth/registration boilerplate and a service-style
``serve_forever`` loop.

It is **self-healing across broker restarts**: the broker re-registers a
polling agent's mailbox automatically (see ``broker.server`` auto-register on
poll), so when the broker is updated (e.g. by Watchtower) or restarts for a
cert renewal, a long-lived ``BrokeredAgent`` recovers on its own — no manual
restart of the consumer is required.
"""

from __future__ import annotations

import logging
import ssl
import threading
from typing import TYPE_CHECKING, Any

from ..protocol import Message
from ..transport.brokered import create_transport_pair
from .agent import Agent, NotificationHandler, RequestHandler

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

__all__ = ["BrokeredAgent"]


class BrokeredAgent:
    """A pull/mailbox-mode agent bound to a secured broker.

    Args:
        agent_id: This agent's id on the broker (must match its bearer token's
            principal when the broker enforces auth).
        broker_host: Broker hostname (e.g. ``ai-broker.robotsix.net``).
        broker_token: This agent's bearer token (``None`` only for an
            auth-disabled broker, e.g. in tests).
        broker_port: Broker port (default 443).
        broker_scheme: ``"https"`` (default) or ``"http"``.
        tls_ca: Optional path to a custom CA PEM for a privately-signed broker
            certificate. When omitted, the system trust store is used (the
            deployed broker is fronted by a publicly-trusted endpoint).
        ssl_context: Optional explicit ``ssl.SSLContext`` (overrides *tls_ca*).
        timeout: Per-request timeout (seconds) for sends and the poll loop.
        on_request: Optional inbound-request handler (returns a reply
            :class:`Message`).
        on_notification: Optional inbound-notification handler.
        max_handler_workers: Maximum handler pool threads (default 4).

    Use it as a context manager, or call :meth:`start`/:meth:`stop`. For a
    long-lived service, :meth:`serve_forever` blocks until ``SIGTERM``/``SIGINT``.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        broker_host: str,
        broker_token: str | None,
        broker_port: int = 443,
        broker_scheme: str = "https",
        tls_ca: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = 30.0,
        on_request: RequestHandler | None = None,
        on_notification: NotificationHandler | None = None,
        max_handler_workers: int = 4,
    ) -> None:
        """Initialize the brokered agent with broker connection settings."""
        if ssl_context is None and tls_ca:
            ssl_context = ssl.create_default_context(cafile=tls_ca)

        self.agent_id = agent_id
        registry, transport = create_transport_pair(
            "brokered",
            broker_host=broker_host,
            broker_port=broker_port,
            broker_scheme=broker_scheme,
            broker_ssl_context=ssl_context,
            broker_token=broker_token,
        )
        self._agent = Agent(
            agent_id,
            registry,
            transport=transport,
            pull=True,
            timeout=timeout,
            max_handler_workers=max_handler_workers,
        )
        if on_request is not None:
            self._agent.on_request(on_request)
        if on_notification is not None:
            self._agent.on_notification(on_notification)

    # -- handler registration (also usable as decorators) -----------------

    def on_request(self, handler: RequestHandler) -> RequestHandler:
        """Register the inbound-request handler; returns it."""
        return self._agent.on_request(handler)

    def on_notification(self, handler: NotificationHandler) -> NotificationHandler:
        """Register the inbound-notification handler; returns it."""
        return self._agent.on_notification(handler)

    # -- sending ----------------------------------------------------------

    def send_request(
        self,
        recipient: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        **extra: Any,
    ) -> Message:
        """Send a request and return the correlated reply."""
        return self._agent.send_request(
            recipient,
            dict(body) if body is not None else None,
            timeout=timeout,
            **extra,
        )

    def send_notification(
        self,
        recipient: str,
        body: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """Send a fire-and-forget notification."""
        self._agent.send_notification(
            recipient, dict(body) if body is not None else None, **extra
        )

    def _registration_capabilities(self) -> dict[str, object]:
        """Override in subclasses to advertise agent capabilities at registration.

        Returned dict is snapshotted when :meth:`start` is called; changes
        made after ``start()`` are not reflected at the broker until the
        agent is restarted.
        """
        return {}

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Register the mailbox and start the background receive loop."""
        self._agent.start(capabilities=self._registration_capabilities())
        logger.info("BrokeredAgent %r started (pull/mailbox mode)", self.agent_id)

    def stop(self) -> None:
        """Stop receiving and unregister."""
        self._agent.stop()
        logger.info("BrokeredAgent %r stopped", self.agent_id)

    def __enter__(self) -> BrokeredAgent:
        """Enter the runtime context, starting the agent."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the runtime context, stopping the agent."""
        self.stop()

    def serve_forever(self) -> None:
        """Start the agent and block until ``SIGTERM``/``SIGINT``.

        Convenience for service entrypoints. Must be called from the main
        thread (it installs signal handlers). Stops the agent cleanly on exit.
        """
        import signal

        stop_event = threading.Event()

        def _handle_signal(signum: int, _frame: Any) -> None:
            logger.info("BrokeredAgent %r received signal %d", self.agent_id, signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        self.start()
        logger.info("BrokeredAgent %r serving; awaiting shutdown signal", self.agent_id)
        try:
            stop_event.wait()
        finally:
            self.stop()
