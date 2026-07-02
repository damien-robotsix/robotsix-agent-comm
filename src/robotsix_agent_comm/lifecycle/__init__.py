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

from .backend import MockBackend, SubprocessBackend
from .config import LifecycleConfig
from .server import LifecycleServer
from .service import build_server
from .supervision import (
    AlertHandler,
    Incident,
    IncidentKind,
    ServiceState,
    SupervisionAgent,
    SupervisionConfig,
    build_supervisor,
)
from .tracing import LifecycleTracing

__all__ = [
    "AlertHandler",
    "Incident",
    "IncidentKind",
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
