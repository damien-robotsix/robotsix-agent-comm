"""Broker server: register, deregister, discovery, send, health.

The server wraps :class:`http.server.ThreadingHTTPServer` and reuses the
existing transport primitives (:class:`Registry`, :class:`TransportClient`,
:class:`Router`, :class:`RetryPolicy`) without modifying them.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlsplit

from ..protocol import (
    Error,
    Message,
    ProtocolError,
    deserialize,
    serialize,
)
from ..transport import (
    AgentNotFoundError,
    DeliveryError,
    Endpoint,
    Registry,
    RetryPolicy,
    Router,
    TransportClient,
)
from ..transport.endpoints import HEALTH_PATH

# ---------------------------------------------------------------------------
# Private HTTP server subclass
# ---------------------------------------------------------------------------


class _BrokerHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server that carries the broker dispatch state."""

    daemon_threads = True

    registry: Registry
    capabilities_lock: threading.Lock
    capabilities: dict[str, dict[str, object]]
    router: Router

    # Heartbeat / TTL eviction state (child 3)
    heartbeat_lock: threading.Lock
    last_heartbeat: dict[str, float]
    ttl_seconds: dict[str, int]
    default_ttl_seconds: int


# ---------------------------------------------------------------------------
# Private request handler
# ---------------------------------------------------------------------------


class _BrokerRequestHandler(BaseHTTPRequestHandler):
    """Routes ``/agents``, ``/messages``, and ``/health`` endpoints."""

    def _server(self) -> _BrokerHTTPServer:
        return cast("_BrokerHTTPServer", self.server)

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def _write_serialized(
        self, status: int, payload: str, content_type: str = "application/json"
    ) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8")

    def _parse_agent_id_from_path(self) -> str:
        """Extract the agent id from a ``/agents/<id>`` path.

        Returns the empty string when the path ends with ``/agents/`` or
        ``/agents`` (no trailing id).
        """
        path = self.path
        # Strip any query/fragment the stdlib might pass through.
        parsed = urlsplit(path)
        clean = parsed.path
        prefix = "/agents/"
        if clean.startswith(prefix):
            return clean[len(prefix) :]
        return ""

    def _route_send(self, message: Message) -> tuple[int, str | None]:
        """Deliver ``message`` via the broker's router.

        Returns ``(http_status, body_str | None)`` where *body_str* is a
        pre-serialised JSON response (message or error envelope).
        """
        server = self._server()
        try:
            reply = server.router.route(message)
        except AgentNotFoundError:
            # Only raised by Router when lookup fails — we pre-check
            # empty recipient earlier.
            error = Error.to(
                message,
                code="unknown_recipient",
                message=f"unknown recipient: {message.metadata.recipient}",
            )
            return (404, serialize(error))
        except DeliveryError as exc:
            error = Error.to(
                message,
                code="delivery_failed",
                message=str(exc),
            )
            return (502, serialize(error))

        if reply is None:
            return (204, None)
        return (200, serialize(reply))

    # ------------------------------------------------------------------
    # HTTP method dispatchers
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self.path == HEALTH_PATH:
            self._write_json(200, {"status": "ok"})
            return

        if self.path == "/agents":
            server = self._server()
            with server.capabilities_lock:
                agents = [
                    {"agent_id": agent_id, "capabilities": dict(caps)}
                    for agent_id, caps in server.capabilities.items()
                ]
            self._write_json(200, {"agents": agents})
            return

        self._write_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/agents":
            self._handle_register()
            return

        if self.path == "/messages":
            self._handle_send()
            return

        self._write_error(404, "not found")

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/agents/"):
            self._handle_deregister()
            return
        self._write_error(404, "not found")

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def _handle_register(self) -> None:
        """Handle ``POST /agents`` — register or update an agent."""
        raw = self._read_body()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._write_error(400, "invalid JSON body")
            return

        if not isinstance(data, dict):
            self._write_error(400, "body must be a JSON object")
            return

        agent_id = data.get("agent_id")
        host = data.get("host")
        port = data.get("port")

        if not isinstance(agent_id, str) or not agent_id:
            self._write_error(
                400, "agent_id is required and must be a non-empty string"
            )
            return
        if not isinstance(host, str) or not host:
            self._write_error(400, "host is required and must be a non-empty string")
            return
        if not isinstance(port, int):
            self._write_error(400, "port is required and must be an integer")
            return

        scheme = data.get("scheme", "http")
        path = data.get("path", "/messages")
        capabilities = data.get("capabilities")

        endpoint = Endpoint(
            agent_id=agent_id,
            host=host,
            port=port,
            scheme=str(scheme) if isinstance(scheme, str) else "http",
            path=str(path) if isinstance(path, str) else "/messages",
        )

        server = self._server()
        # Determine whether this is a new registration or an update.
        try:
            server.registry.lookup(agent_id)
            is_new = False
        except AgentNotFoundError:
            is_new = True

        server.registry.register(endpoint)

        caps: dict[str, object] = {}
        if isinstance(capabilities, dict):
            caps = dict(capabilities)
        with server.capabilities_lock:
            server.capabilities[agent_id] = caps

        # Record heartbeat and TTL
        ttl_val = data.get("ttl_seconds")
        with server.heartbeat_lock:
            server.last_heartbeat[agent_id] = time.monotonic()
            if isinstance(ttl_val, int):
                server.ttl_seconds[agent_id] = ttl_val
            elif is_new:
                server.ttl_seconds[agent_id] = server.default_ttl_seconds
            # else: re-registration without explicit TTL — keep existing

        self._write_json(201 if is_new else 200, {"agent_id": agent_id})

    def _handle_deregister(self) -> None:
        """Handle ``DELETE /agents/{id}`` — idempotent removal."""
        agent_id = self._parse_agent_id_from_path()
        if not agent_id:
            self._write_error(400, "missing agent_id in path")
            return

        server = self._server()
        with contextlib.suppress(AgentNotFoundError):
            server.registry.unregister(agent_id)

        with server.capabilities_lock:
            server.capabilities.pop(agent_id, None)

        with server.heartbeat_lock:
            server.last_heartbeat.pop(agent_id, None)
            server.ttl_seconds.pop(agent_id, None)

        self.send_response(204)
        self.end_headers()

    def _handle_send(self) -> None:
        """Handle ``POST /messages`` — deserialise, validate, route."""
        raw = self._read_body()
        try:
            message = deserialize(raw)
        except ProtocolError as exc:
            self._write_error(400, str(exc))
            return

        # Empty/missing recipient → 400
        recipient = message.metadata.recipient
        if not recipient:
            self._write_error(400, "metadata.recipient is required")
            return

        # Check that the recipient is registered (before routing).
        server = self._server()
        try:
            server.registry.lookup(recipient)
        except AgentNotFoundError:
            error = Error.to(
                message,
                code="unknown_recipient",
                message=f"unknown recipient: {recipient}",
            )
            self._write_serialized(404, serialize(error))
            return

        http_status, body_str = self._route_send(message)
        if body_str is not None:
            self._write_serialized(http_status, body_str)
        else:
            self.send_response(http_status)
            self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        """Silence the default stderr request logging."""


# ---------------------------------------------------------------------------
# Public BrokerServer
# ---------------------------------------------------------------------------

DEFAULT_RETRY_POLICY = RetryPolicy(max_attempts=1, base_delay=0.0, max_delay=0.0)


class BrokerServer:
    """Standalone agent-comm broker daemon.

    Wraps an HTTP server that exposes register / deregister / discovery /
    send / health endpoints, reusing the existing ``Registry``,
    ``TransportClient``, and ``Router`` from ``robotsix_agent_comm.transport``.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        ttl_seconds: int = 60,
        sweep_interval_seconds: float = 30.0,
        router_timeout: float = 5.0,
        router_retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._server = _BrokerHTTPServer((host, port), _BrokerRequestHandler)

        # -- Registry shared with the request handler --
        self._server.registry = Registry()

        # -- Capabilities storage --
        self._server.capabilities_lock = threading.Lock()
        self._server.capabilities = {}

        # -- Heartbeat / TTL eviction state --
        self._server.heartbeat_lock = threading.Lock()
        self._server.last_heartbeat = {}
        self._server.ttl_seconds = {}
        self._server.default_ttl_seconds = ttl_seconds

        # -- Router for message delivery --
        retry_policy = (
            router_retry_policy
            if router_retry_policy is not None
            else DEFAULT_RETRY_POLICY
        )
        self._server.router = Router(
            self._server.registry,
            TransportClient(),
            retry_policy,
            timeout=router_timeout,
        )

        self._sweep_interval_seconds = sweep_interval_seconds
        self._sweep_stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sweep_thread: threading.Thread | None = None

    # -- Lifecycle (matches TransportServer) --

    @property
    def host(self) -> str:
        """Return the bound host address."""
        return cast("tuple[str, int]", self._server.server_address)[0]

    @property
    def port(self) -> int:
        """Return the actually-bound port (useful when ``port=0``)."""
        return cast("tuple[str, int]", self._server.server_address)[1]

    def start(self) -> None:
        """Serve requests on a background daemon thread (idempotent)."""
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

        # Start sweep thread when enabled (sweep_interval_seconds > 0).
        if self._sweep_interval_seconds > 0:
            self._sweep_thread = threading.Thread(target=self._sweep_loop, daemon=True)
            self._sweep_thread.start()

    def stop(self) -> None:
        """Stop serving and release the socket."""
        self._sweep_stop_event.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._sweep_thread is not None:
            self._sweep_thread.join()
            self._sweep_thread = None

    # -- Sweep (heartbeat-based TTL eviction) ---------------------------

    def _sweep_loop(self) -> None:
        """Run periodic sweep until ``_sweep_stop_event`` is set."""
        while True:
            if self._sweep_stop_event.wait(self._sweep_interval_seconds):
                break  # event was set — shut down
            self._sweep_once()

    def _sweep_once(self) -> None:
        """Evict every agent whose TTL has elapsed since last heartbeat."""
        server = self._server
        now = time.monotonic()

        # Snapshot current registrations (acquires & releases registry._lock).
        endpoints = server.registry.list_agents()

        for endpoint in endpoints:
            agent_id = endpoint.agent_id

            # Read heartbeat state under heartbeat_lock.
            with server.heartbeat_lock:
                last_hb = server.last_heartbeat.get(agent_id)
                ttl = server.ttl_seconds.get(agent_id)

            # No heartbeat data or TTL → skip (defensive).
            if last_hb is None or ttl is None:
                continue

            # TTL <= 0 means no expiry.
            if ttl <= 0:
                continue

            # Not expired yet.
            if now - last_hb <= ttl:
                continue

            # Evict: registry, capabilities, heartbeat bookkeeping.
            with contextlib.suppress(AgentNotFoundError):
                server.registry.unregister(agent_id)

            with server.capabilities_lock:
                server.capabilities.pop(agent_id, None)

            with server.heartbeat_lock:
                server.last_heartbeat.pop(agent_id, None)
                server.ttl_seconds.pop(agent_id, None)

    def close(self) -> None:
        """Alias for :meth:`stop`."""
        self.stop()

    def __enter__(self) -> BrokerServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
