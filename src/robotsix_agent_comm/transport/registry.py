"""In-memory agent endpoint registry.

The registry maps an ``agent_id`` to its :class:`Endpoint` for
registration and discovery. It is guarded by a :class:`threading.Lock`
because the transport server dispatches on worker threads.
"""

from __future__ import annotations

import threading

from .endpoints import Endpoint
from .errors import AgentNotFoundError


class Registry:
    """Thread-safe, in-memory map of ``agent_id`` to :class:`Endpoint`."""

    def __init__(self) -> None:
        """Initialize an empty thread-safe registry."""
        self._lock = threading.Lock()
        self._endpoints: dict[str, Endpoint] = {}

    def register(
        self,
        endpoint: Endpoint,
        *,
        capabilities: dict[str, object] | None = None,
    ) -> None:
        """Register (or replace) ``endpoint`` under its ``agent_id``.

        *capabilities* are accepted for compatibility with
        :class:`BrokeredRegistry` but ignored in the in-process registry.
        """
        with self._lock:
            self._endpoints[endpoint.agent_id] = endpoint

    def unregister(self, agent_id: str) -> None:
        """Remove ``agent_id`` from the registry.

        Raises:
            AgentNotFoundError: if ``agent_id`` is not registered.
        """
        with self._lock:
            try:
                del self._endpoints[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"unknown agent: {agent_id!r}") from exc

    def lookup(self, agent_id: str) -> Endpoint:
        """Return the endpoint registered for ``agent_id``.

        Raises:
            AgentNotFoundError: if ``agent_id`` is not registered.
        """
        with self._lock:
            try:
                return self._endpoints[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"unknown agent: {agent_id!r}") from exc

    def list_agents(self) -> list[Endpoint]:
        """Return a snapshot list of all registered endpoints."""
        with self._lock:
            return list(self._endpoints.values())
