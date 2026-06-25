"""Lifecycle server: deploy, rollback, deployment history.

The server wraps :class:`http.server.ThreadingHTTPServer` and exposes
a JSON API for managing service deployments.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlsplit

from .backend import LifecycleBackend
from .store import DeploymentRevision, DeploymentStore

# ---------------------------------------------------------------------------
# Private HTTP server subclass
# ---------------------------------------------------------------------------


class _LifecycleHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server that carries the lifecycle dispatch state."""

    daemon_threads = True

    backend: LifecycleBackend
    store: DeploymentStore
    auth_token: str | None
    health_timeout_seconds: float
    health_interval_seconds: float
    health_check_enabled: bool


# ---------------------------------------------------------------------------
# Private request handler
# ---------------------------------------------------------------------------


class _LifecycleRequestHandler(BaseHTTPRequestHandler):
    """Routes service endpoints and health check.

    Handles ``/services/{name}/deploy``, ``/rollback``,
    ``/deployments``, and ``/health``.
    """

    def _server(self) -> _LifecycleHTTPServer:
        return cast("_LifecycleHTTPServer", self.server)

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def _read_body(self) -> str | None:
        """Read the request body.

        Returns the decoded body string, or ``None`` when the body is
        missing (a 400 JSON error is already written).  Returns ``""``
        for a legitimate empty body.
        """
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._write_error(400, "request body is required")
            return None
        # Limit to 1 MiB for safety.
        if length > 1_048_576:
            self._write_error(413, "request body too large")
            return None
        return self.rfile.read(length).decode("utf-8")

    def _authenticate(self) -> str | None:
        """Validate the ``Authorization: Bearer <token>`` header.

        Returns the authenticated ``"admin"`` on success, or the
        sentinel ``""`` when authentication is disabled.  Writes a
        ``401`` JSON error response and returns ``None`` on failure.
        """
        server = self._server()

        if server.auth_token is None:
            return ""  # auth disabled — allow all

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._write_error(401, "missing or invalid Authorization header")
            return None

        token = auth_header[len("Bearer "):]
        if token != server.auth_token:
            self._write_error(401, "invalid token")
            return None

        return "admin"

    # ------------------------------------------------------------------
    # HTTP method dispatchers
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)

        # Health probe is intentionally unauthenticated.
        if parsed.path == "/health":
            self._write_json(200, {"status": "ok"})
            return

        agent = self._authenticate()
        if agent is None:
            return

        # GET /services/{name}/deployments
        if parsed.path.endswith("/deployments"):
            service_name = self._parse_service_name(parsed.path, "/deployments")
            if service_name is None:
                self._write_error(
                    400,
                    "invalid path: expected /services/{name}/deployments",
                )
                return
            self._handle_get_deployments(service_name)
            return

        self._write_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        agent = self._authenticate()
        if agent is None:
            return

        parsed = urlsplit(self.path)

        # POST /services/{name}/deploy
        if parsed.path.endswith("/deploy"):
            service_name = self._parse_service_name(parsed.path, "/deploy")
            if service_name is None:
                self._write_error(
                    400, "invalid path: expected /services/{name}/deploy"
                )
                return
            self._handle_deploy(service_name)
            return

        # POST /services/{name}/rollback
        if parsed.path.endswith("/rollback"):
            service_name = self._parse_service_name(parsed.path, "/rollback")
            if service_name is None:
                self._write_error(
                    400, "invalid path: expected /services/{name}/rollback"
                )
                return
            self._handle_rollback(service_name)
            return

        self._write_error(404, "not found")

    # ------------------------------------------------------------------
    # Path parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_service_name(path: str, suffix: str) -> str | None:
        """Extract the service name from ``/services/<name><suffix>``.

        Returns ``None`` when the path does not match the expected pattern.
        """
        prefix = "/services/"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        name = path[len(prefix): -len(suffix)]
        if not name:
            return None
        return name

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def _handle_deploy(self, service_name: str) -> None:
        """Handle ``POST /services/{name}/deploy``.

        Deploys *version* for *service_name*, health-checks it, and
        auto-rolls back to the last healthy revision on failure.
        """
        raw = self._read_body()
        if raw is None:
            return

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._write_error(400, "invalid JSON body")
            return

        if not isinstance(data, dict):
            self._write_error(400, "body must be a JSON object")
            return

        target_version = data.get("version")
        if not isinstance(target_version, str) or not target_version:
            self._write_error(400, "version is required and must be a non-empty string")
            return

        server = self._server()
        store = server.store
        backend = server.backend

        # Serialize per service.
        with store.lock(service_name):
            # Remember the last good revision for auto-rollback.
            last_good = store.get_current(service_name)

            # Create and record the new deployment revision.
            revision = DeploymentRevision(
                service_name=service_name,
                revision_id=str(uuid.uuid4()),
                version=target_version,
                timestamp=time.time(),
                source="deploy",
                status="PENDING",
                previous_revision_id=last_good.revision_id if last_good else None,
            )
            store.add_revision(revision)
            store.set_current(service_name, revision.revision_id)

        # Deploy outside the lock so health checks don't block other services.
        try:
            backend.start(service_name, version=target_version)
        except Exception as exc:
            with store.lock(service_name):
                revision.status = "UNHEALTHY"
            self._write_error(502, f"deploy failed: {exc}")
            return

        # Health-gated promotion.
        if server.health_check_enabled:
            healthy = self._wait_for_healthy(service_name)
        else:
            healthy = True

        if healthy:
            with store.lock(service_name):
                revision.status = "HEALTHY"
            self._write_json(
                200,
                {
                    "service": service_name,
                    "revision_id": revision.revision_id,
                    "version": target_version,
                    "status": "HEALTHY",
                },
            )
            return

        # -- Auto-rollback -------------------------------------------------
        with store.lock(service_name):
            revision.status = "UNHEALTHY"

            if last_good is None:
                self._write_error(
                    502,
                    "deploy failed health check and no previous good revision "
                    "to roll back to",
                )
                return

            # Stop the failed service, then start the last good version.
            with contextlib.suppress(Exception):
                backend.stop(service_name)  # best-effort stop

            try:
                backend.start(service_name, version=last_good.version)
            except Exception as exc:
                self._write_error(
                    502,
                    f"deploy failed health check and rollback start failed: {exc}",
                )
                return

            # Health-check the rollback target.
            if server.health_check_enabled:
                rollback_healthy = self._wait_for_healthy(service_name)
            else:
                rollback_healthy = True

            # Create a rollback revision for the auto-rollback.
            rollback_rev = DeploymentRevision(
                service_name=service_name,
                revision_id=str(uuid.uuid4()),
                version=last_good.version,
                timestamp=time.time(),
                source="rollback",
                status="HEALTHY" if rollback_healthy else "UNHEALTHY",
                previous_revision_id=revision.revision_id,
            )
            store.add_revision(rollback_rev)
            store.set_current(service_name, rollback_rev.revision_id)

        if rollback_healthy:
            self._write_json(
                200,
                {
                    "service": service_name,
                    "revision_id": rollback_rev.revision_id,
                    "version": last_good.version,
                    "status": "HEALTHY",
                    "rolled_back": True,
                },
            )
        else:
            self._write_error(
                502,
                "deploy failed health check and rollback target is also unhealthy",
            )

    def _handle_rollback(self, service_name: str) -> None:
        """Handle ``POST /services/{name}/rollback``.

        Rolls back to a previous revision (the immediate predecessor by
        default, or an explicit *revision_id*).
        """
        raw = self._read_body()
        if raw is None:
            return

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._write_error(400, "invalid JSON body")
            return

        if not isinstance(data, dict):
            self._write_error(400, "body must be a JSON object")
            return

        explicit_revision_id = data.get("revision_id")
        if explicit_revision_id is not None and not isinstance(
            explicit_revision_id, str
        ):
            self._write_error(400, "revision_id must be a string")
            return

        server = self._server()
        store = server.store
        backend = server.backend

        with store.lock(service_name):
            current = store.get_current(service_name)

            if explicit_revision_id is not None:
                target = store.get_revision(service_name, explicit_revision_id)
                if target is None:
                    self._write_error(
                        404, f"revision {explicit_revision_id!r} not found"
                    )
                    return
            else:
                if current is None:
                    self._write_error(400, "no current deployment to roll back from")
                    return
                if current.previous_revision_id is None:
                    self._write_error(400, "no previous revision to roll back to")
                    return
                target = store.get_revision(
                    service_name, current.previous_revision_id
                )
                if target is None:
                    self._write_error(
                        500, "previous revision not found in store"
                    )
                    return

            # Stop current, start target.
            with contextlib.suppress(Exception):
                backend.stop(service_name)  # best-effort stop

            try:
                backend.start(service_name, version=target.version)
            except Exception as exc:
                self._write_error(502, f"rollback start failed: {exc}")
                return

            # Create a rollback revision.
            rollback_rev = DeploymentRevision(
                service_name=service_name,
                revision_id=str(uuid.uuid4()),
                version=target.version,
                timestamp=time.time(),
                source="rollback",
                status="HEALTHY",
                previous_revision_id=current.revision_id if current else None,
            )
            store.add_revision(rollback_rev)
            store.set_current(service_name, rollback_rev.revision_id)

        self._write_json(
            200,
            {
                "service": service_name,
                "revision_id": rollback_rev.revision_id,
                "version": target.version,
                "status": "HEALTHY",
            },
        )

    def _handle_get_deployments(self, service_name: str) -> None:
        """Handle ``GET /services/{name}/deployments``."""
        server = self._server()
        store = server.store
        history = store.get_history(service_name)

        current = store.get_current(service_name)
        current_id = current.revision_id if current else None

        deployments = []
        for rev in history:
            deployments.append(
                {
                    "revision_id": rev.revision_id,
                    "version": rev.version,
                    "timestamp": rev.timestamp,
                    "source": rev.source,
                    "status": rev.status,
                    "previous_revision_id": rev.previous_revision_id,
                    "current": rev.revision_id == current_id,
                }
            )

        self._write_json(
            200,
            {
                "service": service_name,
                "deployments": deployments,
            },
        )

    # ------------------------------------------------------------------
    # Health polling
    # ------------------------------------------------------------------

    def _wait_for_healthy(self, service_name: str) -> bool:
        """Poll :meth:`LifecycleBackend.health` until success or timeout.

        Returns ``True`` when the service becomes healthy within the
        configured timeout.
        """
        server = self._server()
        deadline = time.monotonic() + server.health_timeout_seconds

        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if server.backend.health(service_name):
                    return True
            time.sleep(server.health_interval_seconds)

        return False

    def log_message(self, _format: str, *_args: object) -> None:
        """Silence the default stderr request logging."""


# ---------------------------------------------------------------------------
# Public LifecycleServer
# ---------------------------------------------------------------------------


class LifecycleServer:
    """Standalone lifecycle management HTTP daemon.

    Exposes deploy, rollback, and deployment-history endpoints for
    managed suite services.

    Parameters:
        backend:
            The :class:`LifecycleBackend` implementation used to
            start/stop/health-check services.
        store:
            The :class:`DeploymentStore` used to persist revision
            history.
        host:
            Bind address (default ``"0.0.0.0"``).
        port:
            Bind port (default ``0`` = OS-assigned).
        auth_token:
            Optional bearer token required on every mutating endpoint.
            When ``None``, authentication is disabled.
        health_timeout_seconds:
            Maximum time to wait for a service to become healthy after
            deploy (default 30.0).
        health_interval_seconds:
            Interval between health-check polls (default 2.0).
        health_check_enabled:
            When ``False``, skip health checks entirely (default
            ``True`` — useful for tests that want to control health
            behaviour externally).
    """

    def __init__(
        self,
        *,
        backend: LifecycleBackend,
        store: DeploymentStore | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        auth_token: str | None = None,
        health_timeout_seconds: float = 30.0,
        health_interval_seconds: float = 2.0,
        health_check_enabled: bool = True,
    ) -> None:
        self._store = store or DeploymentStore()
        self._backend = backend

        self._http_server = _LifecycleHTTPServer(
            (host, port), _LifecycleRequestHandler
        )

        # Attach shared state to the HTTP server instance.
        self._http_server.backend = backend
        self._http_server.store = self._store
        self._http_server.auth_token = auth_token
        self._http_server.health_timeout_seconds = health_timeout_seconds
        self._http_server.health_interval_seconds = health_interval_seconds
        self._http_server.health_check_enabled = health_check_enabled

        self._thread: threading.Thread | None = None

    # -- Properties --------------------------------------------------------

    @property
    def host(self) -> str:
        """Return the bound host address."""
        return cast("tuple[str, int]", self._http_server.server_address)[0]

    @property
    def port(self) -> int:
        """Return the actually-bound port (useful when ``port=0``)."""
        return cast("tuple[str, int]", self._http_server.server_address)[1]

    @property
    def store(self) -> DeploymentStore:
        """Return the :class:`DeploymentStore` backing this server."""
        return self._store

    @property
    def backend(self) -> LifecycleBackend:
        """Return the :class:`LifecycleBackend` used by this server."""
        return self._backend

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Serve requests on a background daemon thread (idempotent)."""
        if self._thread is not None:
            return
        thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        )
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop serving and release the socket."""
        self._http_server.shutdown()
        self._http_server.server_close()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    # -- Context manager ---------------------------------------------------

    def __enter__(self) -> LifecycleServer:
        """Enter the runtime context, starting the server."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the runtime context, stopping the server."""
        self.stop()
