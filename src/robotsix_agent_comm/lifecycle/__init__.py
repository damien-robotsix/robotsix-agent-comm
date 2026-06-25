"""Lifecycle server package.

Exports the public API for the central deployment & lifecycle management
component:

* :class:`LifecycleConfig` — immutable configuration from environment variables.
* :class:`LifecycleServer` — brokered responder exposing status and lifecycle handlers.
* :class:`LifecycleTracing` — Langfuse instrumentation wrapper.
* :func:`build_server` — convenience factory (defined in ``service.py``).
* :class:`SupervisionAgent` — continuously monitors managed services and reacts
    to failures with auto-restart and escalation.
* :class:`SupervisionConfig` — immutable configuration for the supervision agent.
* :func:`build_supervisor` — convenience factory for :class:`SupervisionAgent`.
"""

from __future__ import annotations

from .config import LifecycleConfig
from .server import LifecycleServer
from .tracing import LifecycleTracing
from .supervision import (
    Incident,
    ServiceState,
    SupervisionAgent,
    SupervisionConfig,
    build_supervisor,
)

__all__ = [
    "Incident",
    "LifecycleConfig",
    "LifecycleServer",
    "LifecycleTracing",
    "MockBackend",
    "ServiceState",
    "SubprocessBackend",
    "SupervisionAgent",
    "SupervisionConfig",
    "build_server",
    "build_supervisor",
]


def __getattr__(name: str) -> object:
    """Lazy-import forward references for not-yet-loaded submodules.

    For example, ``build_server`` is defined in ``service.py`` but is
    listed in ``__all__`` so the public API is self-describing even
    before every module is imported.
    """
    if name == "build_server":
        from .service import build_server as _build_server

        return _build_server
    if name in ("MockBackend", "SubprocessBackend"):
        from .backend import MockBackend, SubprocessBackend

        if name == "MockBackend":
            return MockBackend
        return SubprocessBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
