"""Lifecycle server — central deployment & lifecycle management responder.

:class:`LifecycleServer` extends the brokered-responder base to register
with the agent-comm broker and expose status / lifecycle request surfaces
with optional Langfuse tracing.
"""

from __future__ import annotations

import logging
from typing import Any

from ..protocol import Request
from ..sdk.responder import BrokeredResponder
from .config import LifecycleConfig
from .tracing import LifecycleTracing

logger = logging.getLogger(__name__)

__all__ = ["LifecycleServer"]


class LifecycleServer(BrokeredResponder):
    """Central deployment & lifecycle server registered on the agent-comm broker.

    The server exposes:

    * ``"monitor"`` — built-in health check (override of
      :meth:`~BrokeredResponder.handle_monitor`).
    * ``"status"`` — custom status handler (registered via
      :meth:`~BrokeredResponder.register_handler`).
    * ``"lifecycle"`` — accepts lifecycle commands such as restarting a
      named service.

    Every handler is wrapped with :class:`LifecycleTracing` when tracing
    is enabled.

    Args:
        config: Immutable lifecycle configuration providing agent identity
            and broker connection parameters.
        tracing: Langfuse tracing wrapper; operates in no-op mode when
            credentials are missing or the SDK is not installed.
    """

    def __init__(
        self,
        config: LifecycleConfig,
        tracing: LifecycleTracing,
    ) -> None:
        """Initialise the lifecycle server.

        Forwards broker connection details from *config* to the parent
        :class:`BrokeredResponder`, stores the tracing instance, and
        registers the ``"status"`` and ``"lifecycle"`` custom handlers.
        """
        super().__init__(
            agent_id=config.agent_id,
            broker_host=config.broker_host,
            broker_port=config.broker_port,
            broker_scheme=config.broker_scheme,
            broker_token=config.broker_token,
            tls_ca=config.broker_tls_ca,
        )
        self.tracing: LifecycleTracing = tracing

        # Register additional request kinds.
        self.register_handler("status", self.handle_status)
        self.register_handler("lifecycle", self.handle_lifecycle)

    # ------------------------------------------------------------------
    # Built-in handler override
    # ------------------------------------------------------------------

    def handle_monitor(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Return live telemetry, tracing the check when enabled."""
        return self._build_status_result("monitor", "monitor-check")

    # ------------------------------------------------------------------
    # Custom handlers
    # ------------------------------------------------------------------

    def handle_status(self, request: Request, params: dict[str, Any]) -> dict[str, Any]:
        """Return server status with tracing."""
        return self._build_status_result("status", "status-check")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_status_result(self, check_name: str, span_name: str) -> dict[str, Any]:
        """Build a status result dict with optional tracing.

        Args:
            check_name: Span name for the tracing span.
            span_name: Event name logged inside the span.

        Returns:
            A dict with ``status``, ``agent_id``, and
            ``tracing_enabled`` keys.
        """
        result: dict[str, Any] = {
            "status": "ok",
            "agent_id": self.agent_id,
            "tracing_enabled": self.tracing.enabled,
        }

        if self.tracing.enabled:
            with self.tracing.trace(check_name) as span:
                span.event(span_name)
                return result

        return result

    def handle_lifecycle(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle a lifecycle command (e.g. restart a named service).

        Expects *params* to contain optional ``"command"`` and
        ``"service"`` keys.

        Args:
            request: The inbound :class:`~protocol.Request`.
            params: A dict expected to carry ``command`` and ``service``.

        Returns:
            A dict with ``result``, ``command``, and ``service`` keys.
        """
        command: str = params.get("command", "unknown")
        service: str = params.get("service", "unknown")

        result: dict[str, Any] = {
            "result": "acknowledged",
            "command": command,
            "service": service,
        }

        if self.tracing.enabled:
            with self.tracing.trace("lifecycle") as span:
                span.event(
                    "lifecycle-command",
                    metadata={"command": command, "service": service},
                )
                return result

        return result
