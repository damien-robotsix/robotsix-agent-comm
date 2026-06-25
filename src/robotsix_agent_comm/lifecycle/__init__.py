"""Lifecycle management server for the robotsix suite.

The :class:`LifecycleServer` is a standalone HTTP+JSON daemon that
provides versioned deployment with rollback support for managed suite
services.

:class:`LifecycleConfig` parses ``ROBOTSIX_LIFECYCLE_*`` environment
variables.  :func:`build_lifecycle` constructs a configured
:class:`LifecycleServer` from a config.
"""

from __future__ import annotations

from .backend import LifecycleBackend, MockBackend, SubprocessBackend
from .config import LifecycleConfig
from .server import LifecycleServer
from .service import build_lifecycle
from .store import DeploymentRevision, DeploymentStore

__all__ = [
    "LifecycleBackend",
    "LifecycleConfig",
    "LifecycleServer",
    "MockBackend",
    "SubprocessBackend",
    "DeploymentRevision",
    "DeploymentStore",
    "build_lifecycle",
]
