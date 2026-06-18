"""Client-side networked transport and registry that delegate to a broker.

Provides :class:`NetworkedBrokerTransport` (sends all traffic through the
broker's ``POST /messages`` endpoint) and :class:`BrokeredRegistry`
(delegates register/unregister/lookup/list_agents to the broker's HTTP
API). Together they let agents communicate through a central
:class:`~robotsix_agent_comm.broker.server.BrokerServer` without sharing a
process or an in-memory registry.

The factory :func:`create_transport_pair` returns a matching
``(registry, transport)`` pair for a named mode, so callers can swap
in-process and brokered modes with a single parameter.
"""

from __future__ import annotations

import http.client
import json
import ssl
from typing import Any

from ..protocol import Message, ProtocolError, deserialize, serialize
from .base import Transport
from .client import TransportClient
from .endpoints import Endpoint
from .errors import (
    AgentNotFoundError,
    DeliveryError,
    TransportError,
    TransportTimeoutError,
)
from .registry import Registry

# ---------------------------------------------------------------------------
# NetworkedBrokerTransport
# ---------------------------------------------------------------------------

_JSON_HEADERS = {"Content-Type": "application/json"}

_DELIVERY_FAILED = "delivery_failed"
_UNKNOWN_RECIPIENT = "unknown_recipient"


class _BrokerConnectionMixin:
    """Shared HTTP connection boilerplate for broker clients.

    Provides the ``__init__`` fields, ``broker_url`` property, and
    ``_connect`` method used by both :class:`NetworkedBrokerTransport`
    and :class:`BrokeredRegistry`.
    """

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        *,
        scheme: str = "http",
        ssl_context: ssl.SSLContext | None = None,
        agent_token: str | None = None,
    ) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._scheme = scheme
        self._ssl_context = ssl_context
        self._agent_token = agent_token

    @property
    def broker_url(self) -> str:
        """Return the broker's origin URL."""
        return f"{self._scheme}://{self._broker_host}:{self._broker_port}"

    def _connect(self, timeout: float = 5.0) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(
                self._broker_host,
                self._broker_port,
                timeout=timeout,
                context=self._ssl_context,
            )
        return http.client.HTTPConnection(
            self._broker_host, self._broker_port, timeout=timeout
        )


class NetworkedBrokerTransport(_BrokerConnectionMixin, Transport):
    """A :class:`Transport` that sends all messages through a broker.

    Unlike :class:`TransportClient`, this transport *ignores* the
    ``endpoint.host`` / ``endpoint.port`` / ``endpoint.path`` fields and
    always POSTs to ``{broker_url}/messages``.  The broker routes to the
    final recipient using its own registry.

    Parameters:
        ssl_context:
            Optional :class:`ssl.SSLContext` for TLS connections.  When
            *scheme* is ``"https"`` and this is provided, it is passed as
            the *context* argument to :class:`http.client.HTTPSConnection`.

            For mutual TLS (mTLS), load the client certificate and private
            key onto the context before passing it::

                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.load_verify_locations(cafile="/path/to/ca.pem")
                ctx.load_cert_chain(
                    certfile="/path/to/client.pem",
                    keyfile="/path/to/client-key.pem",
                )
                transport = NetworkedBrokerTransport(..., ssl_context=ctx)

        agent_token:
            Optional bearer token included as an ``Authorization`` header
            on every ``POST /messages`` and ``GET /health`` request.
    """

    def _auth_headers(self) -> dict[str, str]:
        """Return a headers dict with the bearer token when configured."""
        if self._agent_token is not None:
            return {"Authorization": f"Bearer {self._agent_token}"}
        return {}

    def send(
        self,
        message: Message,
        endpoint: Endpoint,
        *,
        timeout: float,
    ) -> Message | None:
        """POST ``message`` to the broker and return any reply.

        The *endpoint* parameter is ignored — routing is determined by the
        broker based on ``message.metadata.recipient``.
        """
        body = serialize(message).encode("utf-8")
        headers = {**_JSON_HEADERS, **self._auth_headers()}
        conn = self._connect(timeout)
        try:
            conn.request("POST", "/messages", body=body, headers=headers)
            response = conn.getresponse()
            status = response.status
            data = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"request to broker timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise TransportError(f"failed to reach broker: {exc}") from exc
        finally:
            conn.close()

        if status == 200:
            try:
                return deserialize(data)
            except ProtocolError as exc:
                raise TransportError(f"invalid response from broker: {exc}") from exc
        if status == 204 or not data:
            return None

        # 4xx/5xx — try to parse an Error envelope.
        try:
            error_msg = deserialize(data)
            if hasattr(error_msg, "body") and isinstance(error_msg.body, dict):
                code = error_msg.body.get("code", "")
                if code == _UNKNOWN_RECIPIENT:
                    raise AgentNotFoundError(
                        f"unknown recipient: {message.metadata.recipient}"
                    )
                if code == _DELIVERY_FAILED:
                    raise DeliveryError(
                        error_msg.body.get("message", "delivery failed")
                    )
        except (ProtocolError, json.JSONDecodeError, AgentNotFoundError, DeliveryError):
            raise
        except Exception:
            pass

        raise TransportError(f"broker returned HTTP {status}: {data}")

    def health_check(self, endpoint: Endpoint, *, timeout: float) -> bool:
        """Return ``True`` if ``GET /health`` on the broker returns 200.

        Returns ``False`` on any connection error instead of raising.
        """
        headers = self._auth_headers()
        conn = self._connect(timeout)
        try:
            conn.request("GET", "/health", headers=headers)
            response = conn.getresponse()
            response.read()
            return response.status == 200
        except OSError:
            return False
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# BrokeredRegistry
# ---------------------------------------------------------------------------


class BrokeredRegistry(_BrokerConnectionMixin):
    """Duck-typed match for :class:`Registry` that delegates to the broker.

    Every method maps to an HTTP call against the broker's REST API,
    so agents using this registry do not need to share a process or
    an in-memory data structure.  Callers that accept ``Registry``
    today will accept a ``BrokeredRegistry`` at runtime with no changes.

    .. note::

        ``lookup()`` and ``list_agents()`` each perform an HTTP request.
        Local caching is deferred to a future optimisation.

    Parameters:
        ssl_context:
            Optional :class:`ssl.SSLContext` for TLS connections.  When
            *scheme* is ``"https"`` and this is provided, it is passed as
            the *context* argument to :class:`http.client.HTTPSConnection`.

            For mutual TLS (mTLS), load the client certificate and private
            key onto the context before passing it::

                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.load_verify_locations(cafile="/path/to/ca.pem")
                ctx.load_cert_chain(
                    certfile="/path/to/client.pem",
                    keyfile="/path/to/client-key.pem",
                )
                registry = BrokeredRegistry(..., ssl_context=ctx)

        agent_token:
            Optional bearer token included as an ``Authorization`` header
            on every ``POST /agents``, ``DELETE /agents/<id>``, and
            ``GET /agents`` request.
    """

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 5.0,
    ) -> tuple[int, Any]:
        """Make an HTTP request to the broker; return ``(status, parsed_body)``."""
        conn = self._connect(timeout)
        try:
            payload: bytes | None = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
            headers: dict[str, str] = {}
            if payload:
                headers["Content-Type"] = "application/json"
            if self._agent_token is not None:
                headers["Authorization"] = f"Bearer {self._agent_token}"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read().decode("utf-8")
            status = response.status
        finally:
            conn.close()

        parsed: Any = None
        if data:
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                parsed = data
        return status, parsed

    def register(self, endpoint: Endpoint) -> None:
        """Register *endpoint* with the broker via ``POST /agents``."""
        body: dict[str, Any] = {
            "agent_id": endpoint.agent_id,
            "host": endpoint.host,
            "port": endpoint.port,
            "scheme": endpoint.scheme,
            "path": endpoint.path,
            "capabilities": {},
        }
        # ttl_seconds is omitted so the broker uses its default.
        self._request("POST", "/agents", body=body)

    def unregister(self, agent_id: str) -> None:
        """Idempotent removal via ``DELETE /agents/{agent_id}``.

        Does **not** raise :class:`AgentNotFoundError` — matches the
        broker's idempotent behaviour so that ``Agent.stop()`` (which wraps
        unregister in ``suppress(AgentNotFoundError)``) is safe.
        """
        self._request("DELETE", f"/agents/{agent_id}")

    def lookup(self, agent_id: str) -> Endpoint:
        """Return a placeholder :class:`Endpoint` for *agent_id*.

        Fetches the full agent list via ``GET /agents`` and searches it.
        Raises :class:`AgentNotFoundError` when *agent_id* is absent.

        The returned endpoint carries only ``agent_id``; host/port/scheme
        are dummy values because :class:`NetworkedBrokerTransport` ignores
        them and routes everything through the broker URL.
        """
        status, parsed = self._request("GET", "/agents")
        if status != 200:
            raise TransportError(f"broker GET /agents returned HTTP {status}: {parsed}")
        agents: list[dict[str, Any]] = (
            parsed.get("agents", []) if isinstance(parsed, dict) else []
        )
        for entry in agents:
            if isinstance(entry, dict) and entry.get("agent_id") == agent_id:
                return Endpoint(
                    agent_id=agent_id,
                    host="broker",
                    port=self._broker_port,
                )
        raise AgentNotFoundError(f"unknown agent: {agent_id!r}")

    def list_agents(self) -> list[Endpoint]:
        """Return placeholder endpoints for every registered agent."""
        status, parsed = self._request("GET", "/agents")
        if status != 200:
            raise TransportError(f"broker GET /agents returned HTTP {status}: {parsed}")
        agents: list[dict[str, Any]] = (
            parsed.get("agents", []) if isinstance(parsed, dict) else []
        )
        result: list[Endpoint] = []
        for entry in agents:
            if isinstance(entry, dict):
                agent_id = entry.get("agent_id", "")
                result.append(
                    Endpoint(
                        agent_id=str(agent_id),
                        host="broker",
                        port=self._broker_port,
                    )
                )
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_transport_pair(
    mode: str,
    *,
    broker_host: str = "127.0.0.1",
    broker_port: int = 0,
    broker_scheme: str = "http",
    broker_ssl_context: ssl.SSLContext | None = None,
    broker_token: str | None = None,
) -> tuple[Registry | BrokeredRegistry, Transport]:
    """Return a ``(registry, transport)`` pair for *mode*.

    ``"in-process"``
        Returns ``(Registry(), TransportClient())`` — the default
        single-process point-to-point transport used since Phase 5.

    ``"brokered"``
        Returns ``(BrokeredRegistry(...), NetworkedBrokerTransport(...))``
        — all traffic goes through the broker at *broker_host*:*broker_port*.

    Raises:
        ValueError: if *mode* is not one of the recognised values.
    """
    if mode == "in-process":
        return (Registry(), TransportClient())
    if mode == "brokered":
        return (
            BrokeredRegistry(
                broker_host,
                broker_port,
                scheme=broker_scheme,
                ssl_context=broker_ssl_context,
                agent_token=broker_token,
            ),
            NetworkedBrokerTransport(
                broker_host,
                broker_port,
                scheme=broker_scheme,
                ssl_context=broker_ssl_context,
                agent_token=broker_token,
            ),
        )
    raise ValueError(f"unknown transport mode: {mode!r}")
