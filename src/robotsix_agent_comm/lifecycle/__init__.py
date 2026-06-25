"""Lifecycle server package.

Exports the public API for the central deployment & lifecycle management
component:

* :class:`LifecycleConfig` — immutable configuration from environment variables.
* :class:`LifecycleServer` — brokered responder exposing status and lifecycle handlers.
* :class:`LifecycleTracing` — Langfuse instrumentation wrapper.
* :func:`build_server` — convenience factory (defined in ``service.py``).
"""

from __future__ import annotations

from .config import LifecycleConfig
from .server import LifecycleServer
from .tracing import LifecycleTracing

__all__ = [
    "LifecycleConfig",
    "LifecycleServer",
    "LifecycleTracing",
    "build_server",
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
