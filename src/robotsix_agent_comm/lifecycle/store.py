"""In-memory deployment revision store with per-service locking.

Provides :class:`DeploymentStore` — a thread-safe store that tracks
deployment revisions per service and the current active revision.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class DeploymentRevision:
    """A single deployment or rollback event for a managed service."""

    service_name: str
    revision_id: str
    version: str
    timestamp: float
    source: str  # "deploy" or "rollback"
    status: str  # "PENDING", "HEALTHY", "UNHEALTHY", "ROLLED_BACK"
    previous_revision_id: str | None = None


class DeploymentStore:
    """In-memory store of deployment revisions.

    Each service gets its own :class:`threading.Lock` to serialize
    deploys and rollbacks so two operations on the same service
    cannot interleave.

    Typical usage::

        store = DeploymentStore()
        with store.lock("my-svc"):
            rev = DeploymentRevision(
                service_name="my-svc",
                revision_id=str(uuid.uuid4()),
                version="v1.2.3",
                timestamp=time.time(),
                source="deploy",
                status="PENDING",
            )
            store.add_revision(rev)
            store.set_current("my-svc", rev.revision_id)
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._revisions: dict[str, list[DeploymentRevision]] = {}
        self._current: dict[str, str] = {}  # service_name -> revision_id
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- Lock management -------------------------------------------------

    def lock(self, service_name: str) -> threading.Lock:
        """Return the per-service lock, creating one lazily if needed.

        The caller must use this lock as a context manager::

            with store.lock("my-svc"):
                ...
        """
        with self._locks_guard:
            if service_name not in self._locks:
                self._locks[service_name] = threading.Lock()
            return self._locks[service_name]

    # -- Revision CRUD ---------------------------------------------------

    def add_revision(self, revision: DeploymentRevision) -> None:
        """Append *revision* to the history for its service."""
        self._revisions.setdefault(revision.service_name, []).append(revision)

    def set_current(self, service_name: str, revision_id: str) -> None:
        """Mark *revision_id* as the current revision for *service_name*."""
        self._current[service_name] = revision_id

    def get_current(self, service_name: str) -> DeploymentRevision | None:
        """Return the current revision for *service_name*, or ``None``."""
        rev_id = self._current.get(service_name)
        if rev_id is None:
            return None
        return self.get_revision(service_name, rev_id)

    def get_history(self, service_name: str) -> list[DeploymentRevision]:
        """Return ordered deployment history for *service_name* (oldest first)."""
        return list(self._revisions.get(service_name, []))

    def get_revision(
        self, service_name: str, revision_id: str
    ) -> DeploymentRevision | None:
        """Return a specific revision by id, or ``None``."""
        for rev in self._revisions.get(service_name, []):
            if rev.revision_id == revision_id:
                return rev
        return None
