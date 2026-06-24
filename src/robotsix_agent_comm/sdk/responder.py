"""Brokered responder base class for serving typed request kinds.

:class:`BrokeredResponder` is a long-lived base class that an embedding
component subclasses to register itself on an existing broker and serve
*several typed request kinds* through a clean, per-kind, overridable
handler API.  It inherits the broker connection, TLS/auth plumbing,
``start()``/``stop()``, context-manager, and ``serve_forever()`` lifecycle
from :class:`~.brokered.BrokeredAgent`.

Body convention
---------------

There is **no native ``kind``/discriminator field in the protocol**.  The
:class:`BrokeredResponder` defines a **body-level convention** that the
requester side must follow when constructing :class:`protocol.Request`
bodies:

* ``"kind"`` (``str``, **required**) — the discriminator selecting which
  handler to invoke.
* ``"params"`` (``dict[str, Any]``, optional, defaults to ``{}``) —
  per-kind arguments passed directly to the handler.

Example request body::

    {"kind": "monitor", "params": {"detail": "full"}}
    {"kind": "config-set", "params": {"max_retries": 3}}

Built-in kinds
--------------

===============  ==================
``kind`` value    Handler method
===============  ==================
``"monitor"``     :meth:`handle_monitor`
``"config-get"``  :meth:`handle_config_get`
``"config-set"``  :meth:`handle_config_set`
===============  ==================

Each built-in handler returns a ``dict[str, Any]`` that becomes the
:class:`~protocol.Response` body.  The base implementations raise
:class:`NotImplementedError` — subclasses override the methods they serve.

Crash-safety
------------

The internal dispatcher (:meth:`_dispatch`) **always** returns a
:class:`~protocol.Message` (a :class:`~protocol.Response` on success, an
:class:`~protocol.Error` on any failure) and **never** lets an exception
propagate out of the handler.  This keeps the background poll loop alive
in pull/mailbox mode.

Error codes returned to the requester:

* ``"invalid_request"`` — body is not a ``dict``, or ``"kind"`` is missing
  or not a string.
* ``"unknown_kind"`` — the kind is not in the dispatch table.
* ``"handler_error"`` — the handler raised an exception; the error message
  carries ``str(exc)``.

All error frames are built with :meth:`protocol.Error.to` so they carry a
``correlation_id`` matching the request and route back to the original
sender.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable
from typing import Any, cast, overload

from ..protocol import Error, Message, Request, Response
from .brokered import BrokeredAgent

logger = logging.getLogger(__name__)

__all__ = ["BrokeredResponder"]


class BrokeredResponder(BrokeredAgent):
    """A brokered agent that dispatches typed requests to overridable handlers.

    Subclass and override :meth:`handle_monitor`, :meth:`handle_config_get`,
    :meth:`handle_config_set` (and any additional kinds registered via
    :meth:`register_handler`) to serve requests without writing your own
    ``if/elif`` dispatch, error framing, or crash-safety guard.

    Constructor kwargs mirror :class:`BrokeredAgent` exactly — the
    broker-connection plumbing, TLS/auth, and lifecycle are inherited
    unchanged.

    Args:
        agent_id: This agent's id on the broker.
        broker_host: Broker hostname.
        broker_token: This agent's bearer token (``None`` only for an
            auth-disabled broker, e.g. in tests).
        broker_port: Broker port (default 443).
        broker_scheme: ``"https"`` (default) or ``"http"``.
        tls_ca: Optional path to a custom CA PEM.
        ssl_context: Optional explicit :class:`ssl.SSLContext`.
        timeout: Per-request timeout in seconds.
    """

    #: Class-level mapping of built-in kind → handler method name.
    #: Subclasses can extend or override this mapping.
    _BUILTIN_HANDLERS: dict[str, str] = {
        "monitor": "handle_monitor",
        "config-get": "handle_config_get",
        "config-set": "handle_config_set",
    }

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
    ) -> None:
        """Initialise the brokered responder with broker connection settings.

        All kwargs are forwarded unchanged to :class:`BrokeredAgent`.
        The internal dispatcher is wired to :meth:`on_request` so
        subclasses must **not** call ``on_request`` themselves.
        """
        super().__init__(
            agent_id,
            broker_host=broker_host,
            broker_token=broker_token,
            broker_port=broker_port,
            broker_scheme=broker_scheme,
            tls_ca=tls_ca,
            ssl_context=ssl_context,
            timeout=timeout,
        )
        # Per-instance extra handlers (populated via register_handler).
        self._extra_handlers: dict[
            str, Callable[[Request, dict[str, Any]], dict[str, Any]]
        ] = {}
        # Wire dispatch — subclasses must NOT call on_request themselves.
        self.on_request(self._dispatch)

    # ------------------------------------------------------------------
    # Registry for additional (non-built-in) kinds
    # ------------------------------------------------------------------

    @overload
    def register_handler(
        self,
        kind: str,
        handler: Callable[[Request, dict[str, Any]], dict[str, Any]],
    ) -> Callable[[Request, dict[str, Any]], dict[str, Any]]: ...

    @overload
    def register_handler(
        self,
        kind: str,
        handler: None = None,
    ) -> Callable[
        [Callable[[Request, dict[str, Any]], dict[str, Any]]],
        Callable[[Request, dict[str, Any]], dict[str, Any]],
    ]: ...

    def register_handler(
        self,
        kind: str,
        handler: Callable[[Request, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> (
        Callable[[Request, dict[str, Any]], dict[str, Any]]
        | Callable[
            [Callable[[Request, dict[str, Any]], dict[str, Any]]],
            Callable[[Request, dict[str, Any]], dict[str, Any]],
        ]
    ):
        """Register a custom handler for *kind*, overriding any built-in.

        Can be used as a plain method or as a decorator::

            responder.register_handler("echo", my_echo)
            # or
            @responder.register_handler("echo")
            def my_echo(request, params):
                ...

        Returns *handler* (or the decorator when *handler* is ``None``).
        """
        if handler is None:
            # Decorator mode: return a callable that registers and returns.
            def _decorator(
                fn: Callable[[Request, dict[str, Any]], dict[str, Any]],
            ) -> Callable[[Request, dict[str, Any]], dict[str, Any]]:
                self._extra_handlers[kind] = fn
                return fn

            return _decorator

        self._extra_handlers[kind] = handler
        return handler

    # ------------------------------------------------------------------
    # Built-in handler methods (override in subclass)
    # ------------------------------------------------------------------

    def handle_monitor(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Return live telemetry / state.

        Override in a subclass to expose component-specific monitoring data.
        """
        raise NotImplementedError("handle_monitor not implemented")

    def handle_config_get(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the current configuration.

        Override in a subclass to expose component-specific config.
        """
        raise NotImplementedError("handle_config_get not implemented")

    def handle_config_set(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply a configuration update and return the new state.

        Override in a subclass to accept component-specific config updates.
        """
        raise NotImplementedError("handle_config_set not implemented")

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, request: Request) -> Message:
        """Dispatch an inbound request to the appropriate handler.

        This method is registered as the inbound request handler via
        ``on_request`` during ``__init__``.  It **always** returns a
        :class:`~protocol.Message` and **never** lets an exception escape —
        keeping the background poll loop alive in pull/mailbox mode.

        Returns:
            A :class:`~protocol.Response` on success, or an
            :class:`~protocol.Error` on any failure.
        """
        # -- Validate body shape -----------------------------------------
        body = request.body
        if not isinstance(body, dict):
            return Error.to(
                request,
                code="invalid_request",
                message="request body must be a JSON object",
                sender=self.agent_id,
            )

        kind = body.get("kind")
        if not isinstance(kind, str) or not kind:
            return Error.to(
                request,
                code="invalid_request",
                message="missing or invalid 'kind' in request body",
                sender=self.agent_id,
            )

        # -- Look up handler ---------------------------------------------
        handler = self._lookup_handler(kind)
        if handler is None:
            return Error.to(
                request,
                code="unknown_kind",
                message=f"unknown request kind: {kind!r}",
                sender=self.agent_id,
            )

        # -- Extract params ---------------------------------------------
        params = body.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # -- Invoke handler (crash-safe) ---------------------------------
        try:
            result = handler(request, params)
        except Exception as exc:
            logger.exception("handler for kind %r raised", kind)
            return Error.to(
                request,
                code="handler_error",
                message=str(exc),
                sender=self.agent_id,
            )

        return Response.to(request, body=result, sender=self.agent_id)

    def _lookup_handler(
        self, kind: str
    ) -> Callable[[Request, dict[str, Any]], dict[str, Any]] | None:
        """Return the handler callable for *kind*, or ``None`` if unknown.

        Instance-level handlers registered via :meth:`register_handler` take
        precedence over built-in method-name handlers, so a subclass can
        override even the built-in kinds dynamically.
        """
        handler = self._extra_handlers.get(kind)
        if handler is not None:
            return handler
        method_name = self._BUILTIN_HANDLERS.get(kind)
        if method_name is not None:
            return cast(
                "Callable[[Request, dict[str, Any]], dict[str, Any]] | None",
                getattr(self, method_name, None),
            )
        return None
