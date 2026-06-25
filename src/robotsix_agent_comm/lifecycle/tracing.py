"""Langfuse instrumentation for the lifecycle server.

Provides :class:`LifecycleTracing` — a lightweight wrapper around the
Langfuse Python SDK that gracefully degrades to a no-op when the SDK is
not installed or credentials are missing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level Langfuse availability flag
# ---------------------------------------------------------------------------

try:
    import langfuse  # noqa: F401

    _HAS_LANGFUSE = True
except ModuleNotFoundError:
    _HAS_LANGFUSE = False


# ---------------------------------------------------------------------------
# No-op / dummy helpers
# ---------------------------------------------------------------------------


class _DummySpan:
    """A do-nothing object that mimics a Langfuse span/trace.

    Supports context-manager usage and the ``.span()`` / ``.event()``
    methods so that no-op mode is transparent to calling code.
    """

    def span(self, name: str, **kwargs: Any) -> _DummySpan:
        """Return a new dummy span (always *self* for simplicity)."""
        return _DummySpan()

    def event(self, name: str, **kwargs: Any) -> None:
        """No-op event."""

    def update(self, **kwargs: Any) -> None:
        """No-op update (matches Langfuse trace/span API)."""

    def __enter__(self) -> _DummySpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# Singleton so that repeated calls don't create unnecessary objects.
_DUMMY = _DummySpan()


# ---------------------------------------------------------------------------
# Tracing wrapper
# ---------------------------------------------------------------------------


class LifecycleTracing:
    """Langfuse instrumentation for lifecycle-server operations.

    If the ``langfuse`` package is not installed, or if *public_key* /
    *secret_key* are falsy (``None`` or empty string), the instance
    operates in **no-op mode**: every method returns a harmless dummy
    object and logs at debug level.

    Parameters:
        public_key:
            Langfuse public key (project-scoped).  If ``None`` or empty
            the tracer disables itself.
        secret_key:
            Langfuse secret key.  If ``None`` or empty the tracer
            disables itself.
        host:
            Langfuse server host URL.  When ``None`` the Langfuse SDK
            default is used (usually ``https://cloud.langfuse.com``).
    """

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
    ) -> None:
        """Initialise the tracing wrapper.

        Args:
            public_key: Langfuse public key (project-scoped).
            secret_key: Langfuse secret key.
            host: Optional Langfuse host URL.
        """
        self._client: Any = None

        # -- Decide whether we can enable Langfuse -----------------------
        if not _HAS_LANGFUSE:
            logger.debug(
                "LifecycleTracing: langfuse package not installed — tracing disabled."
            )
            return

        if not public_key or not secret_key:
            logger.debug(
                "LifecycleTracing: credentials not provided — tracing disabled."
            )
            return

        # -- Create the underlying Langfuse client -----------------------
        try:
            # The SDK accepts ``public_key``, ``secret_key``, and ``host``.
            # ``host`` is optional; when omitted the SDK uses its default.
            init_kwargs: dict[str, Any] = {
                "public_key": public_key,
                "secret_key": secret_key,
            }
            if host is not None:
                init_kwargs["host"] = host

            self._client = langfuse.Langfuse(**init_kwargs)
            logger.info("LifecycleTracing: Langfuse client initialised.")
        except Exception as exc:
            logger.warning(
                "LifecycleTracing: failed to initialise Langfuse client — "
                "tracing disabled.  Error: %s",
                exc,
            )
            self._client = None

    # -- Public API -------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """``True`` when Langfuse is active and ready to record traces."""
        return self._client is not None

    def trace(self, name: str, **kwargs: Any) -> Any:
        """Create a new Langfuse trace.

        Returns a Langfuse trace object that can be used as a context
        manager::

            with tracing.trace("operation-name") as span:
                span.event("something-happened")
                with span.span("child-step") as child:
                    ...

        In no-op mode returns a :class:`_DummySpan` whose context-manager
        and method calls are all harmless.

        Parameters:
            name: Human-readable name for the trace.
            **kwargs: Additional keyword arguments forwarded to
                ``langfuse.Langfuse.trace()`` (e.g. ``user_id``,
                ``session_id``, ``metadata``).

        Returns:
            A Langfuse trace object or a :class:`_DummySpan`.
        """
        if not self.enabled:
            logger.debug("LifecycleTracing.trace(%r) — no-op (disabled).", name)
            return _DUMMY

        logger.debug("LifecycleTracing.trace(%r)", name)
        return self._client.trace(name=name, **kwargs)

    def event(
        self,
        name: str,
        trace_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Emit a point-in-time event (optionally inside an existing trace).

        In no-op mode this is a silent no-op.

        Parameters:
            name: Human-readable event name.
            trace_id: Optional trace identifier to attach the event to.
                When ``None`` the Langfuse SDK may associate the event
                with the currently-active trace (if called inside a
                trace context manager) or create a standalone event.
            **kwargs: Additional keyword arguments forwarded to
                ``langfuse.Langfuse.event()`` (e.g. ``metadata``,
                ``level``).
        """
        if not self.enabled:
            logger.debug("LifecycleTracing.event(%r) — no-op (disabled).", name)
            return

        logger.debug("LifecycleTracing.event(%r, trace_id=%r)", name, trace_id)
        event_kwargs: dict[str, Any] = {"name": name}
        if trace_id is not None:
            event_kwargs["trace_id"] = trace_id
        event_kwargs.update(kwargs)
        self._client.event(**event_kwargs)
