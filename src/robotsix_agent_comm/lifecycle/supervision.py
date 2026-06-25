"""Supervision agent: monitors managed services and reacts to failures.

Background loop that periodically polls each managed service's health
via a :class:`LifecycleBackend`, attempts bounded auto-restart with
exponential backoff, and escalates after N consecutive failures.

Provides an optional broker-integration hook for emitting alerts, an
HTTP status endpoint, and configurable intervals/thresholds/backoff
via ``ROBOTSIX_SUPERVISION_*`` environment variables.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from .backend import LifecycleBackend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert callback type
# ---------------------------------------------------------------------------

AlertHandler = Callable[["Incident"], None]
"""Callback invoked when the supervisor emits an alert/notification.

Receives the :class:`Incident` describing what happened.
"""


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@dataclass
class Incident:
    """A recorded supervision event (degradation, restart, escalation)."""

    timestamp: float
    """Unix timestamp when the incident occurred."""

    service_name: str
    """The service that triggered this incident."""

    kind: str
    """One of ``"degraded"``, ``"restarted"``, ``"escalated"``."""

    message: str
    """Human-readable description."""

    attempt: int | None = None
    """For ``"restarted"``: which restart attempt (1-based).  ``None`` otherwise."""


# ---------------------------------------------------------------------------
# Per-service state
# ---------------------------------------------------------------------------


@dataclass
class ServiceState:
    """Live state the supervisor tracks for one managed service."""

    service_name: str
    healthy: bool = True
    consecutive_failures: int = 0
    restart_count: int = 0
    escalated: bool = False
    last_health_check: float = 0.0
    last_failure: float | None = None
    last_restart: float | None = None
    escalation_time: float | None = None
    incidents: list[Incident] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _parse_bool(raw: str) -> bool:
    """Accept ``1`` / ``true`` / ``yes`` (case-insensitive) as truthy."""
    return raw.strip().lower() in ("1", "true", "yes")


def _parse_service_list(raw: str) -> tuple[str, ...]:
    """Split a comma- or space-separated list into a tuple of stripped strings."""
    if not raw:
        return ()
    parts = raw.replace(",", " ").split()
    return tuple(p for p in parts if p)


@dataclass(frozen=True)
class SupervisionConfig:
    """Immutable configuration for the :class:`SupervisionAgent`.

    Construct via :meth:`from_env` (preferred) or directly with keyword
    arguments (for tests).
    """

    poll_interval_seconds: float = 30.0
    """Seconds between full poll cycles (all services)."""

    health_timeout_seconds: float = 10.0
    """Per-service health-check timeout (seconds)."""

    max_restart_attempts: int = 3
    """Maximum consecutive restart attempts before escalation."""

    backoff_base_seconds: float = 5.0
    """Base delay (seconds) for exponential backoff between restarts."""

    backoff_max_seconds: float = 300.0
    """Maximum backoff cap (seconds)."""

    escalation_cooldown_seconds: float = 600.0
    """Seconds to wait after escalation before polling resumes for that service."""

    services: tuple[str, ...] = ()
    """Names of services to monitor."""

    # -- Status HTTP endpoint -------------------------------------------
    status_host: str = "127.0.0.1"
    status_port: int = 0
    """Bind address for the observability/status HTTP endpoint (``0`` = OS-assigned)."""

    # -- Factory ---------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> SupervisionConfig:
        """Parse configuration from *env* (defaults to ``os.environ``).

        Parameters:
            env:
                An explicit ``Mapping`` of variable names to values, or
                ``None`` to read the real process environment.

        Returns:
            A populated-and-validated :class:`SupervisionConfig`.
        """
        import os

        if env is None:
            env = os.environ

        def _get(key: str, default: str = "") -> str:
            return env.get(key, default)

        config = cls(
            poll_interval_seconds=float(
                _get("ROBOTSIX_SUPERVISION_POLL_INTERVAL_SECONDS", "30.0")
            ),
            health_timeout_seconds=float(
                _get("ROBOTSIX_SUPERVISION_HEALTH_TIMEOUT_SECONDS", "10.0")
            ),
            max_restart_attempts=int(
                _get("ROBOTSIX_SUPERVISION_MAX_RESTART_ATTEMPTS", "3")
            ),
            backoff_base_seconds=float(
                _get("ROBOTSIX_SUPERVISION_BACKOFF_BASE_SECONDS", "5.0")
            ),
            backoff_max_seconds=float(
                _get("ROBOTSIX_SUPERVISION_BACKOFF_MAX_SECONDS", "300.0")
            ),
            escalation_cooldown_seconds=float(
                _get("ROBOTSIX_SUPERVISION_ESCALATION_COOLDOWN_SECONDS", "600.0")
            ),
            services=_parse_service_list(_get("ROBOTSIX_SUPERVISION_SERVICES")),
            status_host=_get("ROBOTSIX_SUPERVISION_STATUS_HOST", "127.0.0.1"),
            status_port=int(_get("ROBOTSIX_SUPERVISION_STATUS_PORT", "0")),
        )

        config.validate()
        return config

    # -- Validation ------------------------------------------------------

    def validate(self) -> None:
        """Validate this config, raising :exc:`ValueError` on failure."""
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.health_timeout_seconds <= 0:
            raise ValueError("health_timeout_seconds must be positive")
        if self.max_restart_attempts < 0:
            raise ValueError("max_restart_attempts must be >= 0")
        if self.backoff_base_seconds <= 0:
            raise ValueError("backoff_base_seconds must be positive")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")
        if self.escalation_cooldown_seconds < 0:
            raise ValueError("escalation_cooldown_seconds must be >= 0")


# ---------------------------------------------------------------------------
# Status HTTP server (private helpers)
# ---------------------------------------------------------------------------


class _StatusHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server that carries the supervisor state."""

    daemon_threads = True
    supervisor: SupervisionAgent


class _StatusRequestHandler(BaseHTTPRequestHandler):
    """Serves ``GET /status`` as a JSON summary."""

    def _sv(self) -> SupervisionAgent:
        return cast("_StatusHTTPServer", self.server).supervisor

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._handle_status()
            return
        self._write_json(404, {"error": "not found"})

    def _handle_status(self) -> None:
        sv = self._sv()
        services = {}
        for name, state in sv.services_state.items():
            services[name] = {
                "healthy": state.healthy,
                "consecutive_failures": state.consecutive_failures,
                "restart_count": state.restart_count,
                "escalated": state.escalated,
                "last_health_check": state.last_health_check,
                "last_failure": state.last_failure,
                "last_restart": state.last_restart,
                "escalation_time": state.escalation_time,
                "recent_incidents": [
                    {
                        "timestamp": inc.timestamp,
                        "kind": inc.kind,
                        "message": inc.message,
                        "attempt": inc.attempt,
                    }
                    for inc in state.incidents[-10:]  # last 10
                ],
            }
        self._write_json(
            200,
            {
                "services": services,
                "running": sv.running,
                "poll_count": sv._poll_count,
            },
        )

    def log_message(self, _format: str, *_args: object) -> None:
        """Silence the default stderr request logging."""


# ---------------------------------------------------------------------------
# SupervisionAgent
# ---------------------------------------------------------------------------


class SupervisionAgent:
    """Continuously monitors managed services and reacts to failures.

    On each poll cycle every service listed in *config.services* is
    health-checked via *backend*.  Unhealthy services trigger the
    restart policy:

    1. First failure — log ``"degraded"`` incident.
    2. Attempt restart via ``backend.stop()`` + ``backend.start()``
       with exponential backoff between attempts.
    3. After *max_restart_attempts* consecutive restart attempts that
       all fail, escalate — log ``"escalated"`` and stop auto-restarting.
       Polling resumes after *escalation_cooldown_seconds*.

    Each incident is forwarded to *on_alert* and to the optional
    broker notification callback.

    Parameters:
        config:
            A validated :class:`SupervisionConfig`.
        backend:
            The :class:`LifecycleBackend` used for health checks and
            restarts.
        on_alert:
            Optional callback invoked for every incident.  Useful for
            wiring broker notifications, Langfuse traces, or custom
            alerting conventions.
    """

    def __init__(
        self,
        config: SupervisionConfig,
        backend: LifecycleBackend,
        *,
        on_alert: AlertHandler | None = None,
    ) -> None:
        """Initialise the supervision agent.

        Args:
            config: Validated configuration.
            backend: Lifecycle backend for health checks and restarts.
            on_alert: Optional alert/notification callback.
        """
        self._config = config
        self._backend = backend
        self._on_alert = on_alert

        # Per-service live state (populated lazily).
        self.services_state: dict[str, ServiceState] = {}
        self._services_lock = threading.Lock()

        # Background loop control.
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._poll_count = 0

        # Status HTTP server.
        self._http: _StatusHTTPServer | None = None
        self._http_thread: threading.Thread | None = None

        # Per-service backoff state (not part of ServiceState to keep it simple).
        self._backoff: dict[str, float] = {}  # service_name → current backoff seconds

        # Escalation cooldown trackers.
        self._escalation_deadline: dict[str, float] = {}

    # -- Properties --------------------------------------------------------

    @property
    def running(self) -> bool:
        """Return ``True`` while the background poll loop is active."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def status_host(self) -> str:
        """Return the bound host of the status HTTP server."""
        if self._http is None:
            return self._config.status_host
        return cast("tuple[str, int]", self._http.server_address)[0]

    @property
    def status_port(self) -> int:
        """Return the bound port of the status HTTP server."""
        if self._http is None:
            return self._config.status_port
        return cast("tuple[str, int]", self._http.server_address)[1]

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start background polling and the status HTTP server (idempotent)."""
        if self._thread is not None:
            return

        # Initialise per-service state.
        with self._services_lock:
            for name in self._config.services:
                if name not in self.services_state:
                    self.services_state[name] = ServiceState(service_name=name)

        # Start status HTTP server.
        self._http = _StatusHTTPServer(
            (self._config.status_host, self._config.status_port),
            _StatusRequestHandler,
        )
        self._http.supervisor = self
        http_thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        http_thread.start()
        self._http_thread = http_thread

        # Start background poll loop.
        self._stop_event.clear()
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()
        self._thread = thread

        logger.info(
            "SupervisionAgent started (services=%s, status=%s:%s)",
            list(self._config.services),
            self.status_host,
            self.status_port,
        )

    def stop(self) -> None:
        """Stop background polling and the status HTTP server."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=2.0)
            self._http_thread = None
        logger.info("SupervisionAgent stopped")

    def __enter__(self) -> SupervisionAgent:
        """Enter the runtime context, starting the agent."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the runtime context, stopping the agent."""
        self.stop()

    # -- Background loop ---------------------------------------------------

    def _poll_loop(self) -> None:
        """Main supervision loop — runs on a daemon thread."""
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()
            self._poll_count += 1

            for service_name in list(self._config.services):
                if self._stop_event.is_set():
                    return
                self._poll_service(service_name)

            # Sleep for the remainder of the interval.
            elapsed = time.monotonic() - cycle_start
            remaining = self._config.poll_interval_seconds - elapsed
            if remaining > 0:
                self._stop_event.wait(remaining)

    def _poll_service(self, service_name: str) -> None:
        """Poll a single service: health-check, restart, or escalate."""
        state = self._get_state(service_name)
        now = time.time()

        # Honour escalation cooldown.
        deadline = self._escalation_deadline.get(service_name)
        if deadline is not None and now < deadline:
            return

        state.last_health_check = now

        # -- Health check --------------------------------------------------
        healthy = False
        deadline_health = time.monotonic() + self._config.health_timeout_seconds
        while time.monotonic() < deadline_health:
            with contextlib.suppress(Exception):
                if self._backend.health(service_name):
                    healthy = True
                    break
            time.sleep(0.5)

        # -- Healthy path --------------------------------------------------
        if healthy:
            if not state.healthy:
                logger.info("Service %r recovered", service_name)
            state.healthy = True
            state.consecutive_failures = 0
            state.escalated = False
            self._backoff.pop(service_name, None)
            self._escalation_deadline.pop(service_name, None)
            return

        # -- Unhealthy path ------------------------------------------------
        state.healthy = False
        state.consecutive_failures += 1
        state.last_failure = now

        if state.escalated:
            # Already escalated — wait for cooldown to expire.
            return

        # First failure → degraded incident.
        if state.consecutive_failures == 1:
            incident = Incident(
                timestamp=now,
                service_name=service_name,
                kind="degraded",
                message=f"Service {service_name!r} is unhealthy",
            )
            state.incidents.append(incident)
            self._emit(incident)

        # Check if we've exceeded the restart threshold.
        if state.consecutive_failures > self._config.max_restart_attempts:
            self._escalate(state, now)
            return

        # -- Attempt restart with backoff ----------------------------------
        self._attempt_restart(state, now)

    def _attempt_restart(self, state: ServiceState, now: float) -> None:
        """Try to restart *state.service_name* with backoff."""
        service_name = state.service_name
        current_backoff = self._backoff.get(service_name, 0.0)

        # Check if we're still in a backoff wait period.
        if state.last_restart is not None:
            elapsed = now - state.last_restart
            if elapsed < current_backoff:
                return  # still waiting

        # Calculate next backoff: exponential, capped.
        if current_backoff == 0.0:
            next_backoff = self._config.backoff_base_seconds
        else:
            next_backoff = min(current_backoff * 2, self._config.backoff_max_seconds)
        self._backoff[service_name] = next_backoff

        # Attempt restart.
        attempt = state.restart_count + 1
        logger.warning(
            "Restarting %r (attempt %d, backoff %.1fs)",
            service_name,
            attempt,
            next_backoff,
        )

        with contextlib.suppress(Exception):
            self._backend.stop(service_name)
        try:
            self._backend.start(service_name)
        except Exception as exc:
            logger.error("Restart of %r failed: %s", service_name, exc)

        state.restart_count = attempt
        state.last_restart = now

        incident = Incident(
            timestamp=now,
            service_name=service_name,
            kind="restarted",
            message=f"Service {service_name!r} restarted (attempt {attempt})",
            attempt=attempt,
        )
        state.incidents.append(incident)
        self._emit(incident)

    def _escalate(self, state: ServiceState, now: float) -> None:
        """Escalate after exceeding max restart attempts."""
        service_name = state.service_name
        state.escalated = True
        state.escalation_time = now

        cooldown = self._config.escalation_cooldown_seconds
        self._escalation_deadline[service_name] = now + cooldown
        self._backoff.pop(service_name, None)

        incident = Incident(
            timestamp=now,
            service_name=service_name,
            kind="escalated",
            message=(
                f"Service {service_name!r} escalated after "
                f"{state.consecutive_failures} consecutive failures "
                f"({state.restart_count} restart attempts); "
                f"cooldown {cooldown:.0f}s"
            ),
        )
        state.incidents.append(incident)
        self._emit(incident)

        logger.error("ESCALATED: %s", incident.message)

    # -- Helpers -----------------------------------------------------------

    def _get_state(self, service_name: str) -> ServiceState:
        with self._services_lock:
            if service_name not in self.services_state:
                self.services_state[service_name] = ServiceState(
                    service_name=service_name
                )
            return self.services_state[service_name]

    def _emit(self, incident: Incident) -> None:
        """Forward *incident* to the alert callback (if configured)."""
        if self._on_alert is not None:
            with contextlib.suppress(Exception):
                self._on_alert(incident)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_supervisor(
    config: SupervisionConfig,
    backend: LifecycleBackend | None = None,
    *,
    on_alert: AlertHandler | None = None,
) -> SupervisionAgent:
    """Build a :class:`SupervisionAgent` from *config*.

    When *backend* is ``None`` a :class:`~.backend.SubprocessBackend` is
    used (production default).  Pass a :class:`~.backend.MockBackend`
    for tests.
    """
    if backend is None:
        from .backend import SubprocessBackend

        backend = SubprocessBackend()

    return SupervisionAgent(config, backend, on_alert=on_alert)
