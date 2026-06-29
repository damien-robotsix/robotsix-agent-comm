"""Broker server: register, deregister, discovery, send, health.

The server wraps :class:`http.server.ThreadingHTTPServer` and reuses the
existing transport primitives (:class:`Registry`, :class:`TransportClient`,
:class:`Router`, :class:`RetryPolicy`) without modifying them.
"""

from __future__ import annotations

import contextlib
import json
import ssl
import threading
import time
from collections import deque
from http.server import ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit

from ..protocol import (
    AgentStatus,
    Error,
    Message,
    ProtocolError,
    TrafficDisposition,
    deserialize,
    serialize,
)
from ..transport import (
    DELIVERY_FAILED,
    UNKNOWN_RECIPIENT,
    AgentNotFoundError,
    DeliveryError,
    Endpoint,
    Registry,
    RetryPolicy,
    Router,
    TransportClient,
)
from ..transport.endpoints import DEFAULT_MESSAGE_PATH, HEALTH_PATH
from ..transport.server import _BaseRequestHandler
from ._audit import _AuditLogger
from ._dashboard import DASHBOARD_HTML
from ._rate_limit import _TokenBucket

#: Upper bound on a single ``GET /messages`` long-poll hold (seconds).
_MAX_POLL_WAIT_SECONDS = 30.0

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

    # Auth state (child 4)
    agent_tokens: dict[str, str] | None
    _token_to_agent: dict[str, str]

    # Body size limit (child 5)
    max_body_size: int = 1_048_576

    # Rate limiting (child 5)
    rate_limit_per_second: float = 0.0
    _rate_buckets: dict[str, _TokenBucket]
    _rate_buckets_lock: threading.Lock

    # Audit logging (child 5)
    _audit_logger: _AuditLogger

    # Mailbox / pull delivery: per-agent queues of serialized messages for
    # agents registered with mailbox=True (no dialable listener — NAT-safe).
    # Guarded by mailbox_cond, which is also used to wake GET /messages
    # long-polls when a message is enqueued.
    mailboxes: dict[str, deque[str]]
    mailbox_cond: threading.Condition

    # Traffic ring buffer (monitoring data layer)
    traffic_buffer: deque[dict[str, object]]
    traffic_lock: threading.Lock

    # Mailbox grace period (defense-in-depth against poll gaps)
    mailbox_grace_seconds: float
    # Dashboard enable flag
    dashboard_enabled: bool


# ---------------------------------------------------------------------------
# Private request handler
# ---------------------------------------------------------------------------


class _BrokerRequestHandler(_BaseRequestHandler):
    """Routes ``/agents``, ``/messages``, and ``/health`` endpoints."""

    def _server(self) -> _BrokerHTTPServer:
        return cast("_BrokerHTTPServer", self.server)

    def _write_error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def _reject(self, status: int, detail: str, agent_id: str = "unknown") -> None:
        """Write an error response and log the rejection."""
        self._write_error(status, detail)
        self._server()._audit_logger.log(
            "register", agent_id, path="/agents", status=status, detail=detail
        )

    def _write_serialized(
        self, status: int, payload: str, content_type: str = "application/json"
    ) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str | None:
        """Read the request body, honouring ``Content-Length``.

        Returns the decoded body string, or ``None`` when the body
        exceeds ``max_body_size`` (a 413 JSON error is already written
        to the client in that case).  Returns ``""`` for a legitimate
        empty body.
        """
        length = int(self.headers.get("Content-Length", 0))
        limit = self._server().max_body_size
        if length > limit:
            self._write_json(
                413,
                {
                    "error": "request body too large",
                    "max_bytes": limit,
                    "received_bytes": length,
                },
            )
            # Still drain the rfile so the connection can be reused.
            self.rfile.read(min(length, limit))
            return None  # sentinel — caller must return early
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
                code=UNKNOWN_RECIPIENT,
                message=f"unknown recipient: {message.metadata.recipient}",
            )
            return (404, serialize(error))
        except DeliveryError as exc:
            error = Error.to(
                message,
                code=DELIVERY_FAILED,
                message=str(exc),
            )
            return (502, serialize(error))

        if reply is None:
            return (204, None)
        return (200, serialize(reply))

    def _authenticate(self, allow_query_token: bool = False) -> str | None:
        """Validate the ``Authorization: Bearer <token>`` header.

        Returns the authenticated ``agent_id`` on success, or the
        sentinel ``""`` when authentication is disabled
        (``server.agent_tokens is None``).  Writes a ``401`` JSON
        error response and returns ``None`` on failure.

        When *allow_query_token* is ``True`` and no ``Authorization``
        header is present, falls back to a ``?token=<tok>`` query
        parameter — this is intended for browser-based dashboard
        access where custom headers cannot be set.
        """
        server = self._server()

        # Auth disabled — allow all requests.
        if server.agent_tokens is None:
            return ""

        auth_header = self.headers.get("Authorization", "")
        if auth_header:
            # Header present — use it authoritatively.
            if not auth_header.startswith("Bearer "):
                self._write_error(401, "missing or invalid Authorization header")
                return None
            token = auth_header[len("Bearer ") :]
            agent_id = server._token_to_agent.get(token)
            if agent_id is None:
                self._write_error(401, "invalid token")
                return None
            return agent_id

        # No Authorization header — optionally fall back to query param.
        if allow_query_token:
            query = parse_qs(urlsplit(self.path).query)
            token = query.get("token", [""])[0]
            if token:
                agent_id = server._token_to_agent.get(token)
                if agent_id is not None:
                    return agent_id

        self._write_error(401, "missing or invalid Authorization header")
        return None

    def _check_rate_limit(self, agent_id: str) -> bool:
        """Check the rate limit for *agent_id*.

        Writes 429 and returns ``False`` when the limit is exceeded.
        Always returns ``True`` when rate limiting is disabled
        (``rate_limit_per_second <= 0``).
        """
        server = self._server()
        if server.rate_limit_per_second <= 0:
            return True

        with server._rate_buckets_lock:
            bucket = server._rate_buckets.get(agent_id)
            if bucket is None:
                bucket = _TokenBucket(server.rate_limit_per_second)
                server._rate_buckets[agent_id] = bucket

        if bucket.consume():
            return True

        body = json.dumps({"error": "rate limit exceeded", "retry_after": 1.0}).encode(
            "utf-8"
        )
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(body)
        return False

    # ------------------------------------------------------------------
    # HTTP method dispatchers
    # ------------------------------------------------------------------

    def _authenticated_and_rate_limited(
        self, allow_query_token: bool = False
    ) -> str | None:
        """Check authentication and rate limit.

        Return agent_id if allowed, else None.
        """
        agent_id = self._authenticate(allow_query_token=allow_query_token)
        if agent_id is None:
            return None
        self._authenticated_agent_id = agent_id

        rate_key = agent_id if agent_id != "" else "__anonymous__"
        if not self._check_rate_limit(rate_key):
            return None
        return agent_id

    @staticmethod
    def _build_agent_entry(
        registry: Registry,
        agent_id: str,
        caps: dict[str, object],
        hb_snapshot: dict[str, float],
        ttl_snapshot: dict[str, int],
        now_monotonic: float,
    ) -> dict[str, object]:
        """Build a single agent entry for the /agents response.

        Pure function — no side effects.
        """
        entry: dict[str, object] = {
            "agent_id": agent_id,
            "capabilities": dict(caps),
        }

        # Last-seen age (monotonic clock).
        last_hb = hb_snapshot.get(agent_id)
        if last_hb is not None:
            entry["last_seen_seconds_ago"] = now_monotonic - last_hb
        else:
            entry["last_seen_seconds_ago"] = None

        # TTL.
        ttl = ttl_snapshot.get(agent_id)
        entry["ttl_seconds"] = ttl

        # Status.
        if ttl is not None and ttl <= 0:
            entry["status"] = AgentStatus.ACTIVE
        elif last_hb is not None and ttl is not None:
            age = now_monotonic - last_hb
            entry["status"] = AgentStatus.ACTIVE if age <= ttl else AgentStatus.STALE
        else:
            entry["status"] = AgentStatus.UNKNOWN

        # Mailbox flag.
        try:
            ep = registry.lookup(agent_id)
            entry["mailbox"] = ep.mailbox
        except AgentNotFoundError:
            entry["mailbox"] = False

        return entry

    def do_GET(self) -> None:  # noqa: N802
        """Dispatch ``GET`` requests: health, agents, traffic, dashboard, or poll."""
        # Health probe is intentionally unauthenticated so Docker HEALTHCHECK
        # and external liveness monitors can reach it without a bearer token.
        if self.path == HEALTH_PATH:
            self._write_json(200, {"status": "ok"})
            return

        # Routes that accept a ?token= query param for browser access.
        _dashboard_reading_paths = frozenset({"/dashboard", "/", "/agents", "/traffic"})
        parsed_path = urlsplit(self.path).path
        allow_query_token = parsed_path in _dashboard_reading_paths

        agent_id = self._authenticated_and_rate_limited(
            allow_query_token=allow_query_token
        )
        if agent_id is None:
            return

        if parsed_path == "/agents":
            server = self._server()
            now_monotonic = time.monotonic()

            # Snapshot heartbeat state under heartbeat_lock.
            with server.heartbeat_lock:
                hb_snapshot = dict(server.last_heartbeat)
                ttl_snapshot = dict(server.ttl_seconds)

            with server.capabilities_lock:
                agents = []
                for agent_id, caps in server.capabilities.items():
                    entry = self._build_agent_entry(
                        server.registry,
                        agent_id,
                        caps,
                        hb_snapshot,
                        ttl_snapshot,
                        now_monotonic,
                    )
                    agents.append(entry)

            self._write_json(200, {"agents": agents})
            return

        if urlsplit(self.path).path == "/traffic":
            self._handle_traffic()
            return

        if urlsplit(self.path).path == DEFAULT_MESSAGE_PATH:
            self._handle_poll(agent_id)
            return

        # -- Dashboard routes (only when enabled) -----------------------
        server = self._server()
        if server.dashboard_enabled and parsed_path in (
            "/dashboard",
            "/",
        ):
            self._write_serialized(
                200, DASHBOARD_HTML, content_type="text/html; charset=utf-8"
            )
            return

        self._write_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        """Dispatch ``POST`` requests: agent registration or message sending."""
        agent_id = self._authenticated_and_rate_limited()
        if agent_id is None:
            return

        if self.path == "/agents":
            self._handle_register()
            return

        if self.path == DEFAULT_MESSAGE_PATH:
            self._handle_send()
            return

        self._write_error(404, "not found")

    def do_DELETE(self) -> None:  # noqa: N802
        """Dispatch ``DELETE`` requests: agent deregistration."""
        agent_id = self._authenticated_and_rate_limited()
        if agent_id is None:
            return

        if self.path.startswith("/agents/"):
            self._handle_deregister()
            return
        self._write_error(404, "not found")

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def _validate_register_payload(
        self, data: dict[str, object], authenticated_agent_id: str
    ) -> dict[str, object] | None:
        """Validate register payload fields and return a dict of parsed values.

        Returns ``None`` on any validation failure (the error response is
        already written via ``_reject``).
        """
        agent_id = data.get("agent_id")
        host = data.get("host")
        port = data.get("port")

        # Mailbox (pull) agents have no dialable listener; the broker queues
        # their messages for GET /messages. host/port are then optional.
        mailbox_flag = bool(data.get("mailbox", False))
        if mailbox_flag:
            host = host if isinstance(host, str) and host else "mailbox"
            port = port if isinstance(port, int) and 1 <= port <= 65535 else 0

        if not isinstance(agent_id, str) or not agent_id:
            self._reject(400, "agent_id is required and must be a non-empty string")
            return None

        if len(agent_id) > 255:
            self._reject(
                400,
                "agent_id must not exceed 255 characters",
                agent_id=agent_id,
            )
            return None

        # When auth is enabled, the body agent_id must match the token.
        if authenticated_agent_id != "" and agent_id != authenticated_agent_id:
            self._reject(
                403,
                "agent_id does not match token",
                agent_id=authenticated_agent_id,
            )
            return None

        if not mailbox_flag:
            if not isinstance(host, str) or not host:
                self._reject(
                    400,
                    "host is required and must be a non-empty string",
                    agent_id=agent_id,
                )
                return None

            if len(host) > 253:
                self._reject(
                    400,
                    "host must not exceed 253 characters",
                    agent_id=agent_id,
                )
                return None

            if not isinstance(port, int):
                self._reject(
                    400,
                    "port is required and must be an integer",
                    agent_id=agent_id,
                )
                return None

            if not 1 <= port <= 65535:
                self._reject(
                    400,
                    "port must be between 1 and 65535",
                    agent_id=agent_id,
                )
                return None

        # -- scheme validation --
        if "scheme" in data:
            scheme_raw = data["scheme"]
            if not isinstance(scheme_raw, str) or scheme_raw not in ("http", "https"):
                self._reject(
                    400,
                    "scheme must be 'http' or 'https'",
                    agent_id=agent_id,
                )
                return None
            scheme = scheme_raw
        else:
            scheme = "http"

        # -- path validation --
        if "path" in data:
            path_raw = data["path"]
            if not isinstance(path_raw, str) or not path_raw.startswith("/"):
                self._reject(
                    400,
                    "path must be a string starting with '/'",
                    agent_id=agent_id,
                )
                return None
            path = path_raw
        else:
            path = "/messages"

        # -- capabilities validation --
        capabilities = data.get("capabilities")
        caps: dict[str, object] = {}
        if "capabilities" in data:
            if not isinstance(capabilities, dict):
                self._reject(
                    400,
                    "capabilities must be a JSON object",
                    agent_id=agent_id,
                )
                return None
            caps = dict(capabilities)
        # else: caps remains {}

        # -- ttl_seconds validation --
        ttl_val = data.get("ttl_seconds")
        if "ttl_seconds" in data and (not isinstance(ttl_val, int) or ttl_val < 0):
            self._reject(
                400,
                "ttl_seconds must be a non-negative integer",
                agent_id=agent_id,
            )
            return None

        # Determine whether this is a new registration or an update.
        server = self._server()
        try:
            server.registry.lookup(agent_id)
            is_new = False
        except AgentNotFoundError:
            is_new = True

        # narrow for the type checker
        assert isinstance(host, str)
        assert isinstance(port, int)

        return {
            "agent_id": agent_id,
            "host": host,
            "port": port,
            "scheme": scheme,
            "path": path,
            "caps": caps,
            "ttl_val": ttl_val,
            "mailbox_flag": mailbox_flag,
            "is_new": is_new,
        }

    def _handle_register(self) -> None:
        """Handle ``POST /agents`` — register or update an agent."""
        raw = self._read_body()
        if raw is None:
            return  # 413 already written by _read_body

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._reject(400, "invalid JSON body")
            return

        if not isinstance(data, dict):
            self._reject(400, "body must be a JSON object")
            return

        validated = self._validate_register_payload(data, self._authenticated_agent_id)
        if validated is None:
            return

        agent_id = cast(str, validated["agent_id"])
        host = cast(str, validated["host"])
        port = cast(int, validated["port"])
        scheme = cast(str, validated["scheme"])
        path = cast(str, validated["path"])
        caps = cast("dict[str, object]", validated["caps"])
        ttl_val = validated["ttl_val"]
        mailbox_flag = cast(bool, validated["mailbox_flag"])
        is_new = cast(bool, validated["is_new"])

        # host/port are validated str/int for endpoint agents, and defaulted to
        # str/int above for mailbox agents — narrow for the type checker.
        assert isinstance(host, str)
        assert isinstance(port, int)
        endpoint = Endpoint(
            agent_id=agent_id,
            host=host,
            port=port,
            scheme=scheme,
            path=path,
            mailbox=mailbox_flag,
        )

        server = self._server()
        server.registry.register(endpoint)

        if mailbox_flag:
            with server.mailbox_cond:
                server.mailboxes.setdefault(agent_id, deque())

        with server.capabilities_lock:
            server.capabilities[agent_id] = caps

        # Record heartbeat and TTL
        with server.heartbeat_lock:
            server.last_heartbeat[agent_id] = time.monotonic()
            if "ttl_seconds" in data:
                assert isinstance(ttl_val, int)  # validated above
                server.ttl_seconds[agent_id] = ttl_val
            elif is_new:
                server.ttl_seconds[agent_id] = server.default_ttl_seconds
            # else: re-registration without explicit TTL — keep existing

        status = 201 if is_new else 200
        server._audit_logger.log(
            "register",
            agent_id,
            path="/agents",
            status=status,
            detail="created" if is_new else "updated",
        )
        self._write_json(status, {"agent_id": agent_id})

    def _handle_deregister(self) -> None:
        """Handle ``DELETE /agents/{id}`` — idempotent removal."""
        agent_id = self._parse_agent_id_from_path()
        if not agent_id:
            self._write_error(400, "missing agent_id in path")
            self._server()._audit_logger.log(
                "deregister",
                "unknown",
                path=self.path,
                status=400,
                detail="missing agent_id in path",
            )
            return

        # When auth is enabled, the path agent_id must match the token.
        if (
            self._authenticated_agent_id != ""
            and agent_id != self._authenticated_agent_id
        ):
            self._write_error(403, "agent_id does not match token")
            self._server()._audit_logger.log(
                "deregister",
                self._authenticated_agent_id,
                path=self.path,
                status=403,
                detail="agent_id does not match token",
            )
            return

        server = self._server()
        with contextlib.suppress(AgentNotFoundError):
            server.registry.unregister(agent_id)

        with server.capabilities_lock:
            server.capabilities.pop(agent_id, None)

        with server.heartbeat_lock:
            server.last_heartbeat.pop(agent_id, None)
            server.ttl_seconds.pop(agent_id, None)

        with server.mailbox_cond:
            server.mailboxes.pop(agent_id, None)
            server.mailbox_cond.notify_all()

        server._audit_logger.log(
            "deregister",
            agent_id,
            path=f"/agents/{agent_id}",
            status=204,
            detail="deregistered",
        )
        self.send_response(204)
        self.end_headers()

    def _record_traffic(
        self,
        *,
        message: Message,
        disposition: str,
        status: int,
    ) -> None:
        body_size_bytes = len(json.dumps(message.body))
        record: dict[str, object] = {
            "timestamp": time.time(),
            "source": message.metadata.sender,
            "destination": message.metadata.recipient,
            "type": message.type.value,
            "topic": message.metadata.extra.get("topic"),
            "message_id": message.message_id,
            "correlation_id": message.correlation_id,
            "body_size_bytes": body_size_bytes,
            "disposition": disposition,
            "status": status,
        }
        server = self._server()
        with server.traffic_lock:
            server.traffic_buffer.append(record)

    def _handle_send(self) -> None:
        """Handle ``POST /messages`` — deserialise, validate, route."""
        raw = self._read_body()
        if raw is None:
            return  # 413 already written by _read_body

        try:
            message = deserialize(raw)
        except ProtocolError as exc:
            self._write_error(400, str(exc))
            self._server()._audit_logger.log(
                "send",
                self._authenticated_agent_id,
                path="/messages",
                status=400,
                detail=f"deserialization error: {exc}",
            )
            return

        # When auth is enabled, sender must match the authenticated agent.
        if (
            self._authenticated_agent_id != ""
            and message.metadata.sender != self._authenticated_agent_id
        ):
            self._record_traffic(
                message=message, disposition=TrafficDisposition.REJECTED, status=403
            )
            self._write_error(403, "sender does not match authenticated agent")
            self._server()._audit_logger.log(
                "send",
                self._authenticated_agent_id,
                path="/messages",
                status=403,
                detail="sender does not match authenticated agent",
            )
            return

        # Empty/missing recipient → 400
        recipient = message.metadata.recipient
        if not recipient:
            self._record_traffic(
                message=message, disposition=TrafficDisposition.REJECTED, status=400
            )
            self._write_error(400, "metadata.recipient is required")
            self._server()._audit_logger.log(
                "send",
                self._authenticated_agent_id,
                path="/messages",
                status=400,
                detail="missing recipient",
            )
            return

        # Check that the recipient is registered (before routing).
        server = self._server()
        try:
            recipient_ep = server.registry.lookup(recipient)
        except AgentNotFoundError:
            error = Error.to(
                message,
                code=UNKNOWN_RECIPIENT,
                message=f"unknown recipient: {recipient}",
            )
            self._record_traffic(
                message=message,
                disposition=TrafficDisposition.UNKNOWN_RECIPIENT,
                status=404,
            )
            self._write_serialized(404, serialize(error))
            server._audit_logger.log(
                "send",
                self._authenticated_agent_id,
                path="/messages",
                status=404,
                detail=f"unknown recipient: {recipient}",
            )
            return

        # Mailbox (pull) recipient: enqueue and let the agent fetch it via
        # GET /messages. NAT-safe — the broker never dials the recipient.
        if recipient_ep.mailbox:
            with server.mailbox_cond:
                server.mailboxes.setdefault(recipient, deque()).append(
                    serialize(message)
                )
                server.mailbox_cond.notify_all()
            self._record_traffic(
                message=message, disposition=TrafficDisposition.QUEUED, status=202
            )
            server._audit_logger.log(
                "send",
                self._authenticated_agent_id,
                path="/messages",
                status=202,
                detail=f"queued for mailbox {recipient}",
            )
            self.send_response(202)
            self.end_headers()
            return

        http_status, body_str = self._route_send(message)
        self._record_traffic(
            message=message, disposition=TrafficDisposition.ROUTED, status=http_status
        )
        server._audit_logger.log(
            "send",
            self._authenticated_agent_id,
            path="/messages",
            status=http_status,
            detail=f"recipient={recipient}",
        )
        if body_str is not None:
            self._write_serialized(http_status, body_str)
        else:
            self.send_response(http_status)
            self.end_headers()

    def _auto_register_mailbox(self, agent_id: str) -> None:
        """Re-create a mailbox registration for a polling pull agent.

        The broker has no record of this agent — e.g. after a restart
        cleared the in-memory registry.

        Mirrors the mailbox path of ``POST /agents`` (idempotent): registry
        entry + empty queue + default TTL/heartbeat. Each lock is taken
        independently (never nested) to avoid deadlocking with the poll path.
        """
        server = self._server()
        server.registry.register(
            Endpoint(agent_id=agent_id, host="mailbox", port=0, mailbox=True)
        )
        with server.mailbox_cond:
            server.mailboxes.setdefault(agent_id, deque())
        with server.capabilities_lock:
            server.capabilities.setdefault(agent_id, {})
        with server.heartbeat_lock:
            server.last_heartbeat[agent_id] = time.monotonic()
            server.ttl_seconds.setdefault(agent_id, server.default_ttl_seconds)
        server._audit_logger.log(
            "auto-register",
            agent_id,
            path="/messages",
            status=200,
            detail="mailbox re-registered on poll",
        )

    def _handle_poll(self, agent_id: str) -> None:
        """Handle ``GET /messages`` — long-poll the caller's mailbox.

        The caller is identified by its auth token (*agent_id*); when auth is
        disabled (*agent_id* is ``""``), an explicit ``?agent_id=`` query
        parameter is required. Blocks up to ``?wait=<seconds>`` (capped by
        :data:`_MAX_POLL_WAIT_SECONDS`) until a message is queued, then returns
        ``{"messages": [<serialized>, ...]}`` (possibly empty on timeout).
        """
        server = self._server()
        query = parse_qs(urlsplit(self.path).query)
        poll_id = agent_id or query.get("agent_id", [""])[0]
        if not poll_id:
            self._write_error(400, "agent_id required (auth token or ?agent_id=)")
            return

        # Self-heal: the broker registry is in-memory, so a restart (image
        # update, cert renewal) loses every registration — but pull agents keep
        # long-polling with a valid token and only POST /agents once, at start.
        # Re-create the caller's mailbox on poll so senders can reach it again
        # without a manual agent restart. When auth is enabled ``poll_id`` is the
        # token-authenticated principal, so a caller can only (re)register itself.
        if poll_id not in server.mailboxes:
            self._auto_register_mailbox(poll_id)

        # A poll counts as a heartbeat so a pull agent stays registered while
        # it is actively listening (it only POSTs /agents once, at start).
        with server.heartbeat_lock:
            if poll_id in server.ttl_seconds:
                server.last_heartbeat[poll_id] = time.monotonic()

        try:
            wait = max(
                0.0, min(_MAX_POLL_WAIT_SECONDS, float(query.get("wait", ["25"])[0]))
            )
        except ValueError:
            wait = 25.0

        with server.mailbox_cond:
            mbox = server.mailboxes.get(poll_id)
            if mbox is None:
                # Caller is not registered as a mailbox agent — nothing to do.
                self._write_json(200, {"messages": []})
                return
            deadline = time.monotonic() + wait
            while not mbox:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                server.mailbox_cond.wait(remaining)
                mbox = server.mailboxes.get(poll_id)
                if mbox is None:  # evicted while waiting
                    self._write_json(200, {"messages": []})
                    return
            messages = list(mbox)
            mbox.clear()
        server._audit_logger.log(
            "poll",
            poll_id,
            path="/messages",
            status=200,
            detail=f"delivered={len(messages)}",
        )
        self._write_json(200, {"messages": messages})

    def _handle_traffic(self) -> None:
        """Handle ``GET /traffic`` — return recent message traffic records."""
        server = self._server()
        query = parse_qs(urlsplit(self.path).query)

        # Parse filters
        agent_filter = query.get("agent", [None])[0]
        topic_filter = query.get("topic", [None])[0]

        since: float | None = None
        until: float | None = None
        with contextlib.suppress(ValueError, TypeError):
            since = float(query.get("since", [""])[0])
        with contextlib.suppress(ValueError, TypeError):
            until = float(query.get("until", [""])[0])

        limit: int | None = None
        try:
            limit = int(query.get("limit", [""])[0])
            if limit <= 0:
                limit = None  # negative/zero → ignore (semantically invalid)
            else:
                limit = min(limit, server.traffic_buffer.maxlen or 1000)
        except (ValueError, TypeError):
            pass

        # Snapshot under lock
        with server.traffic_lock:
            records = list(server.traffic_buffer)

        # Apply filters (outside lock)
        if agent_filter:
            records = [
                r
                for r in records
                if r.get("source") == agent_filter
                or r.get("destination") == agent_filter
            ]
        if topic_filter:
            records = [r for r in records if r.get("topic") == topic_filter]
        if since is not None:
            records = [r for r in records if cast(float, r["timestamp"]) >= since]
        if until is not None:
            records = [r for r in records if cast(float, r["timestamp"]) <= until]
        if limit is not None:
            records = records[-limit:]

        self._write_json(200, {"traffic": records})

    def log_message(self, _format: str, *_args: object) -> None:
        """Silence the default stderr request logging."""


# ---------------------------------------------------------------------------
# Public BrokerServer
# ---------------------------------------------------------------------------

#: Default retry behaviour for the broker's internal router (no retries).
DEFAULT_RETRY_POLICY = RetryPolicy(max_attempts=1, base_delay=0.0, max_delay=0.0)


class BrokerServer:
    """Standalone agent-comm broker daemon.

    Wraps an HTTP server that exposes register / deregister / discovery /
    send / health endpoints, reusing the existing ``Registry``,
    ``TransportClient``, and ``Router`` from ``robotsix_agent_comm.transport``.

    Parameters:
        ssl_context:
            Optional :class:`ssl.SSLContext` for TLS encryption.  When
            provided the server's listening socket is wrapped with
            :meth:`ssl.SSLContext.wrap_socket` so all traffic is
            encrypted.
        require_client_cert:
            When ``True``, the server requests and validates a client
            certificate during the TLS handshake (mutual TLS).  Requires
            *ssl_context* to be provided with a trusted CA loaded via
            :meth:`ssl.SSLContext.load_verify_locations`.  When *ssl_context*
            is ``None`` and this is ``True``, :exc:`ValueError` is raised.
            Defaults to ``False`` (one-way TLS only).
        agent_tokens:
            Optional ``{agent_id: token}`` mapping that enables per-agent
            bearer-token authentication on every endpoint.  When ``None``
            (the default) authentication is disabled and all requests are
            accepted.  When a dict (including an empty one) every request
            must carry a valid ``Authorization: Bearer <token>`` header.
        dashboard_enabled:
            When ``True``, serves the monitoring dashboard at
            ``GET /dashboard`` (and ``GET /``).  Defaults to ``False``
            for production safety.
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
        ssl_context: ssl.SSLContext | None = None,
        require_client_cert: bool = False,
        agent_tokens: dict[str, str] | None = None,
        max_body_size: int = 1_048_576,
        rate_limit_per_second: float = 0.0,
        audit_log_path: str | None = None,
        traffic_buffer_size: int = 1000,
        mailbox_grace_seconds: float = _MAX_POLL_WAIT_SECONDS,
        dashboard_enabled: bool = False,
    ) -> None:
        # -- Validate require_client_cert before binding a socket --
        if require_client_cert and ssl_context is None:
            raise ValueError("require_client_cert requires ssl_context")

        self._server = _BrokerHTTPServer((host, port), _BrokerRequestHandler)

        # -- TLS --
        if ssl_context is not None:
            if require_client_cert:
                ssl_context.verify_mode = ssl.CERT_REQUIRED
            self._server.socket = ssl_context.wrap_socket(
                self._server.socket, server_side=True
            )

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

        # -- Auth state --
        self._server.agent_tokens = agent_tokens
        self._server._token_to_agent = {}
        if agent_tokens is not None:
            for agent_id, token in agent_tokens.items():
                self._server._token_to_agent[token] = agent_id

        # -- Body size limit --
        self._server.max_body_size = max_body_size

        # -- Rate limiting --
        self._server.rate_limit_per_second = rate_limit_per_second
        self._server._rate_buckets = {}
        self._server._rate_buckets_lock = threading.Lock()

        # -- Audit logging --
        self._server._audit_logger = _AuditLogger(audit_log_path)

        # -- Mailbox / pull delivery --
        self._server.mailboxes = {}
        self._server.mailbox_cond = threading.Condition()

        # -- Traffic ring buffer (monitoring data layer) --
        self._server.traffic_buffer = deque(maxlen=traffic_buffer_size)
        self._server.traffic_lock = threading.Lock()

        # -- Mailbox grace period (defense-in-depth against poll gaps) --
        self._server.mailbox_grace_seconds = mailbox_grace_seconds
        # -- Dashboard enable flag --
        self._server.dashboard_enabled = dashboard_enabled

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
        if hasattr(self._server, "_audit_logger"):
            self._server._audit_logger.close()
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
        """Evict every agent whose TTL has elapsed since last heartbeat.

        Mailbox (pull) agents get an extra grace window
        (``mailbox_grace_seconds``) on top of their TTL as defense-in-depth
        against brief poll gaps — a handler that blocks the poll thread for
        longer than the TTL would otherwise cause a spurious eviction.
        """
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

            # Mailbox agents get an extra grace window so a single slow poll
            # (e.g. a handler that briefly starved the poll thread) does not
            # evict them.  Non-mailbox agents use the raw TTL.
            grace = server.mailbox_grace_seconds if endpoint.mailbox else 0.0

            # Not expired yet.
            if now - last_hb <= ttl + grace:
                continue

            # Evict: registry, capabilities, heartbeat bookkeeping.
            with contextlib.suppress(AgentNotFoundError):
                server.registry.unregister(agent_id)

            with server.capabilities_lock:
                server.capabilities.pop(agent_id, None)

            with server.heartbeat_lock:
                server.last_heartbeat.pop(agent_id, None)
                server.ttl_seconds.pop(agent_id, None)

            with server.mailbox_cond:
                server.mailboxes.pop(agent_id, None)
                server.mailbox_cond.notify_all()

    def __enter__(self) -> BrokerServer:
        """Enter the runtime context, starting the server."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the runtime context, stopping the server."""
        self.stop()
