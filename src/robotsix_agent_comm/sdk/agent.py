"""High-level synchronous agent client.

:class:`Agent` composes the Phase-4 ``protocol`` and Phase-5 ``transport``
public APIs into a few-line developer surface: register an agent, send
request-response and fire-and-forget messages, and receive inbound messages
either via callbacks or a pull queue. The shared in-memory :class:`Registry`
is injected so multiple in-process agents discover one another (the
single-host topology Phase 5 ships); networked/shared registries remain out
of scope.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, cast

from ..protocol import (
    Error,
    Message,
    Metadata,
    Notification,
    Request,
)
from ..transport import (
    AgentNotFoundError,
    BrokeredRegistry,
    DeliveryError,
    Endpoint,
    NetworkedBrokerTransport,
    Registry,
    RetryPolicy,
    Router,
    Transport,
    TransportClient,
    TransportError,
    TransportServer,
    TransportTimeoutError,
)

#: Long-poll hold (seconds) the pull receive-loop requests from the broker.
_PULL_POLL_WAIT = 20.0

logger = logging.getLogger(__name__)

RequestHandler = Callable[[Request], Message | None]
"""Callback handling an inbound :class:`Request`; may return a reply."""

NotificationHandler = Callable[[Notification], None]
"""Callback handling an inbound :class:`Notification`; returns nothing."""


class Agent:
    """A communication client bound to a single ``agent_id``.

    The agent owns a :class:`TransportClient` and a :class:`Router`, and on
    :meth:`start` spins up a :class:`TransportServer` registered with the
    shared :class:`Registry`. Transport errors propagate to callers; retries
    are handled inside the router's :class:`RetryPolicy`.
    """

    def __init__(
        self,
        agent_id: str,
        registry: Registry | BrokeredRegistry,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        retry_policy: RetryPolicy | None = None,
        timeout: float = 5.0,
        transport: Transport | None = None,
        pull: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self._registry = registry
        self._host = host
        self._port = port
        self._timeout = timeout
        self._pull = pull
        if retry_policy is None:
            retry_policy = RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=2.0)
        self._client = transport if transport is not None else TransportClient()
        self._router = Router(registry, self._client, retry_policy, timeout=timeout)
        self._server: TransportServer | None = None
        # Pull (mailbox) mode: receive-loop + per-request reply waiters.
        self._recv_thread: threading.Thread | None = None
        self._recv_stop = threading.Event()
        self._waiters: dict[str, tuple[threading.Event, list[Message]]] = {}
        self._waiters_lock = threading.Lock()
        self._request_handler: RequestHandler | None = None
        self._notification_handler: NotificationHandler | None = None
        self._inbox: queue.Queue[Message] = queue.Queue()

    # -- receiving (callbacks) --------------------------------------------

    def on_request(self, handler: RequestHandler) -> RequestHandler:
        """Register ``handler`` for inbound requests; returns ``handler``."""
        self._request_handler = handler
        return handler

    def on_notification(self, handler: NotificationHandler) -> NotificationHandler:
        """Register ``handler`` for inbound notifications; returns it."""
        self._notification_handler = handler
        return handler

    def _handle(self, message: Message) -> Message | None:
        """Dispatch ``message`` to a callback and enqueue it for pull use."""
        self._inbox.put(message)
        if isinstance(message, Request):
            if self._request_handler is not None:
                return self._request_handler(message)
            return Error.to(
                message,
                code="no_handler",
                message=f"agent {self.agent_id!r} has no request handler",
                sender=self.agent_id,
            )
        if isinstance(message, Notification):
            if self._notification_handler is not None:
                self._notification_handler(message)
            return None
        return None

    # -- receiving (pull) -------------------------------------------------

    def receive_message(self, timeout: float | None = None) -> Message:
        """Return the next inbound message from the internal queue.

        Blocks until a message arrives or ``timeout`` seconds elapse.

        Raises:
            TransportTimeoutError: if no message arrives within ``timeout``.
        """
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise TransportTimeoutError(
                f"no message received within {timeout}s"
            ) from exc

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Start receiving and register this agent's endpoint.

        In pull mode the agent registers a *mailbox* (no local listener) and
        receives by long-polling the broker — NAT-safe. Otherwise it opens a
        local :class:`TransportServer` the broker/peers dial into.
        """
        if self._pull:
            if self._recv_thread is not None:
                return
            self._registry.register(
                Endpoint(agent_id=self.agent_id, host="mailbox", port=0, mailbox=True)
            )
            self._recv_stop.clear()
            thread = threading.Thread(target=self._recv_loop, daemon=True)
            thread.start()
            self._recv_thread = thread
            return
        if self._server is not None:
            return
        server = TransportServer(self._handle, host=self._host, port=self._port)
        server.start()
        self._server = server
        self._registry.register(
            Endpoint(agent_id=self.agent_id, host=server.host, port=server.port)
        )

    def stop(self) -> None:
        """Unregister this agent and stop receiving."""
        if self._pull:
            if self._recv_thread is None:
                return
            self._recv_stop.set()
            with contextlib.suppress(AgentNotFoundError):
                self._registry.unregister(self.agent_id)
            self._recv_thread.join(timeout=2.0)
            self._recv_thread = None
            return
        if self._server is None:
            return
        with contextlib.suppress(AgentNotFoundError):
            self._registry.unregister(self.agent_id)
        self._server.stop()
        self._server = None

    # -- pull (mailbox) receive-loop --------------------------------------

    def _recv_loop(self) -> None:
        """Long-poll the broker mailbox and dispatch inbound messages."""
        transport = cast("NetworkedBrokerTransport", self._client)
        while not self._recv_stop.is_set():
            try:
                messages = transport.receive(
                    self.agent_id,
                    wait=_PULL_POLL_WAIT,
                    timeout=_PULL_POLL_WAIT + 10.0,
                )
            except TransportTimeoutError:
                continue
            except (TransportError, OSError):
                # Broker briefly unreachable — back off, then retry.
                self._recv_stop.wait(1.0)
                continue
            for message in messages:
                # A bad message or handler must never kill the receive-loop —
                # that would silently take the whole agent offline.
                try:
                    self._dispatch_pull(message)
                except Exception:  # noqa: BLE001 — last-resort loop guard
                    logger.exception("error dispatching polled message")

    def _dispatch_pull(self, message: Message) -> None:
        """Route a polled message: resolve a pending reply, or handle it."""
        corr = message.correlation_id
        if corr is not None:
            with self._waiters_lock:
                waiter = self._waiters.get(corr)
            if waiter is not None:
                event, slot = waiter
                slot.append(message)
                event.set()
                return
        try:
            reply = self._handle(message)
        except Exception:  # noqa: BLE001 — surface as an Error, never crash
            logger.exception("request handler raised for %s", type(message).__name__)
            reply = (
                Error.to(
                    message,
                    code="handler_error",
                    message="internal handler error",
                    sender=self.agent_id,
                )
                if isinstance(message, Request)
                else None
            )
        if reply is not None:
            # POST the reply back through the broker to the original sender.
            with contextlib.suppress(Exception):
                self._client.send(
                    reply,
                    Endpoint(
                        agent_id=reply.metadata.recipient or "",
                        host="broker",
                        port=0,
                    ),
                    timeout=self._timeout,
                )

    def __enter__(self) -> Agent:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- sending ----------------------------------------------------------

    def send_request(
        self,
        recipient: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        **extra: Any,
    ) -> Message:
        """Send a request to ``recipient`` and return the correlated reply.

        Raises:
            AgentNotFoundError: if ``recipient`` is not registered.
            DeliveryError: if delivery fails or no reply is returned.
            TransportTimeoutError: if the request exceeds its timeout.
        """
        request = Request(
            metadata=Metadata.create(
                sender=self.agent_id, recipient=recipient, **extra
            ),
            body=dict(body) if body is not None else {},
        )
        if self._pull:
            return self._send_request_pull(request, recipient, timeout)
        reply = self._router.route(request, timeout=timeout)
        if reply is None:
            raise DeliveryError(f"no reply received from {recipient!r}")
        return reply

    def _send_request_pull(
        self, request: Request, recipient: str, timeout: float | None
    ) -> Message:
        """Pull-mode request: POST it, then wait for the correlated reply that
        the receive-loop delivers from this agent's own mailbox."""
        key = request.message_id
        event = threading.Event()
        slot: list[Message] = []
        with self._waiters_lock:
            self._waiters[key] = (event, slot)
        try:
            # Endpoint is ignored by the broker transport (routes by recipient).
            self._client.send(
                request,
                Endpoint(agent_id=recipient, host="broker", port=0),
                timeout=self._timeout,
            )
            wait_timeout = self._timeout if timeout is None else timeout
            if not event.wait(wait_timeout):
                raise TransportTimeoutError(
                    f"no reply from {recipient!r} within {wait_timeout}s"
                )
            return slot[0]
        finally:
            with self._waiters_lock:
                self._waiters.pop(key, None)

    def send_notification(
        self,
        recipient: str,
        body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """Send a fire-and-forget notification to ``recipient``.

        Raises:
            AgentNotFoundError: if ``recipient`` is not registered.
            DeliveryError: if delivery fails after retries.
        """
        notification = Notification(
            metadata=Metadata.create(
                sender=self.agent_id, recipient=recipient, **extra
            ),
            body=dict(body) if body is not None else {},
        )
        self._router.route(notification)
