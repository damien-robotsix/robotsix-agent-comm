"""Comprehensive unit tests for :class:`NetworkedBrokerTransport`,
:class:`BrokeredRegistry`, and :func:`create_transport_pair`.

Uses ``unittest.mock.patch`` on ``http.client.HTTPConnection`` so no
real network I/O occurs.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from robotsix_agent_comm.protocol import (
    Error,
    Metadata,
    Notification,
    ProtocolError,
    Request,
    Response,
    serialize,
)
from robotsix_agent_comm.transport import (
    AgentNotFoundError,
    DeliveryError,
    Endpoint,
    Registry,
    TransportClient,
    TransportError,
    TransportTimeoutError,
)
from robotsix_agent_comm.transport.brokered import (
    BrokeredRegistry,
    NetworkedBrokerTransport,
    create_transport_pair,
)

# ---------------------------------------------------------------------------
# Test doubles for http.client
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    """Minimal ``http.client.HTTPResponse`` stand-in."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeHTTPConnection:
    """Minimal ``http.client.HTTPConnection`` stand-in.

    Records the last ``request()`` call in ``_request_args`` and returns
    the pre-configured ``_response`` from ``getresponse()``.  If
    ``_side_effect`` is set it is raised inside ``request()``, allowing
    tests to simulate ``TimeoutError`` and ``OSError``.
    """

    def __init__(
        self,
        response: _FakeHTTPResponse | None = None,
        side_effect: BaseException | None = None,
    ) -> None:
        self._response = response
        self._side_effect = side_effect
        self._request_args: tuple[Any, ...] | None = None

    def request(
        self, method: str, path: str, *, body: Any = None, headers: Any = None
    ) -> None:
        self._request_args = (method, path, body, headers)
        if self._side_effect is not None:
            raise self._side_effect

    def getresponse(self) -> _FakeHTTPResponse:
        if self._response is None:
            return _FakeHTTPResponse(200)
        return self._response

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------


def _endpoint(agent_id: str = "agent-a") -> Endpoint:
    """Return a minimal endpoint — values are ignored by the broker transport."""
    return Endpoint(agent_id=agent_id, host="127.0.0.1", port=9999)


def _request_msg(recipient: str = "agent-a") -> Request:
    return Request(
        metadata=Metadata.create(sender="agent-b", recipient=recipient),
        body={"action": "ping"},
    )


def _notify_msg(recipient: str = "agent-a") -> Notification:
    return Notification(
        metadata=Metadata.create(sender="agent-b", recipient=recipient),
        body={"event": "tick"},
    )


def _patch_http(
    response: _FakeHTTPResponse | None = None,
    side_effect: BaseException | None = None,
) -> Any:
    """Return a ``patch`` context-manager that replaces
    ``http.client.HTTPConnection`` in the ``brokered`` module so that
    every call returns a pre-built :class:`_FakeHTTPConnection`.

    The same connection instance is reused across multiple calls within
    a test.  Use ``mock.return_value`` to inspect it after the fact.
    """
    fake_conn = _FakeHTTPConnection(response=response, side_effect=side_effect)
    return patch(
        "robotsix_agent_comm.transport.brokered.http.client.HTTPConnection",
        return_value=fake_conn,
    )


# ===================================================================
# NetworkedBrokerTransport — send()
# ===================================================================


class TestNetworkedBrokerTransportSend:
    """Unit tests for :meth:`NetworkedBrokerTransport.send`."""

    def test_send_posts_to_messages_with_json_body(self) -> None:
        """``send()`` POSTs to ``/messages`` with the serialized message
        and ``Content-Type: application/json``."""
        request = _request_msg("agent-a")
        expected_body = serialize(request).encode("utf-8")

        reply = Response.to(request, body={"echo": "pong"})
        fake_resp = _FakeHTTPResponse(200, serialize(reply).encode("utf-8"))

        with _patch_http(response=fake_resp) as mock_conn_cls:
            transport = NetworkedBrokerTransport("broker.local", 7000)
            transport.send(request, _endpoint("agent-a"), timeout=5.0)

        # One connection was created.
        mock_conn_cls.assert_called_once()
        # The connection was called with (host, port, timeout=5.0).
        args, kwargs = mock_conn_cls.call_args
        assert args[0] == "broker.local"
        assert args[1] == 7000
        assert kwargs.get("timeout") == 5.0

        # Retrieve the fake connection instance (the shared return_value).
        fake_conn = mock_conn_cls.return_value
        assert fake_conn._request_args is not None
        method, path, body, headers = fake_conn._request_args
        assert method == "POST"
        assert path == "/messages"
        assert body == expected_body
        assert headers == {"Content-Type": "application/json"}

    def test_send_returns_deserialized_reply_on_200(self) -> None:
        """On HTTP 200 the reply is deserialized and returned."""
        request = _request_msg("agent-a")
        reply = Response.to(request, body={"echo": "pong"})
        fake_resp = _FakeHTTPResponse(200, serialize(reply).encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.send(request, _endpoint("agent-a"), timeout=5.0)

        assert result is not None
        assert isinstance(result, Response)
        assert result.body == {"echo": "pong"}
        assert result.correlation_id == request.message_id

    def test_send_returns_none_on_204(self) -> None:
        """On HTTP 204 the method returns ``None`` (fire-and-forget)."""
        fake_resp = _FakeHTTPResponse(204)

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.send(
                _notify_msg("agent-a"), _endpoint("agent-a"), timeout=5.0
            )

        assert result is None

    def test_send_returns_none_on_204_with_empty_body(self) -> None:
        """HTTP 204 with empty body → ``None``."""
        fake_resp = _FakeHTTPResponse(204, b"")

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.send(
                _notify_msg("agent-a"), _endpoint("agent-a"), timeout=5.0
            )

        assert result is None

    def test_send_200_empty_body_raises_transport_error(self) -> None:
        """A 200 with an empty (non-JSON) body cannot be deserialized, so
        :class:`TransportError` is raised (not ``None`` — the ``not data``
        guard is *after* the ``status == 200`` branch)."""
        fake_resp = _FakeHTTPResponse(200, b"")

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="invalid response"):
                transport.send(_request_msg(), _endpoint(), timeout=5.0)

    def test_send_raises_agent_not_found_on_404_unknown_recipient(self) -> None:
        """HTTP 404 with error code ``unknown_recipient`` →
        :class:`AgentNotFoundError`."""
        request = _request_msg("ghost")
        error_envelope = Error.to(
            request,
            code="unknown_recipient",
            message="unknown recipient: ghost",
        )
        fake_resp = _FakeHTTPResponse(404, serialize(error_envelope).encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(AgentNotFoundError, match="unknown recipient"):
                transport.send(request, _endpoint("ghost"), timeout=5.0)

    def test_send_raises_delivery_error_on_502_delivery_failed(self) -> None:
        """HTTP 502 with error code ``delivery_failed`` →
        :class:`DeliveryError`."""
        request = _request_msg("agent-a")
        error_envelope = Error.to(
            request,
            code="delivery_failed",
            message="connection refused",
        )
        fake_resp = _FakeHTTPResponse(502, serialize(error_envelope).encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(DeliveryError, match="connection refused"):
                transport.send(request, _endpoint("agent-a"), timeout=5.0)

    def test_send_raises_transport_error_on_500(self) -> None:
        """A generic 5xx response (that is not 502 with delivery_failed)
        raises :class:`TransportError`.

        Uses a valid serialized message whose body does not contain a
        recognised error code so the parser falls through to the generic
        ``TransportError``.
        """
        # A valid Notification won't have an error code in its body.
        junk_body = serialize(
            Notification(
                metadata=Metadata.create(sender="x", recipient="y"),
                body={"arbitrary": "payload"},
            )
        )
        fake_resp = _FakeHTTPResponse(500, junk_body.encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="HTTP 500"):
                transport.send(_request_msg(), _endpoint(), timeout=5.0)

    def test_send_raises_transport_error_on_403(self) -> None:
        """A 4xx response that is not a recognised error envelope raises
        :class:`TransportError`."""
        junk_body = serialize(
            Notification(
                metadata=Metadata.create(sender="x", recipient="y"),
                body={"arbitrary": "payload"},
            )
        )
        fake_resp = _FakeHTTPResponse(403, junk_body.encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="HTTP 403"):
                transport.send(_request_msg(), _endpoint(), timeout=5.0)

    def test_send_raises_transport_timeout_on_timeouterror(self) -> None:
        """When ``request()`` raises :class:`TimeoutError`, the transport
        raises :class:`TransportTimeoutError`."""
        with _patch_http(side_effect=TimeoutError("timed out")):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportTimeoutError, match="timed out"):
                transport.send(_request_msg(), _endpoint(), timeout=1.0)

    def test_send_raises_transport_error_on_oserror(self) -> None:
        """When ``request()`` raises :class:`OSError` (not TimeoutError),
        the transport raises :class:`TransportError`."""
        with _patch_http(side_effect=OSError("connection refused")):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="failed to reach broker"):
                transport.send(_request_msg(), _endpoint(), timeout=5.0)

    def test_send_raises_transport_error_on_invalid_json_in_200(self) -> None:
        """When the 200 response body cannot be deserialized,
        :class:`TransportError` is raised."""
        fake_resp = _FakeHTTPResponse(200, b"not valid json {{{")

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="invalid response from broker"):
                transport.send(_request_msg(), _endpoint(), timeout=5.0)

    def test_send_404_non_json_body_raises_protocol_error(self) -> None:
        """A 404 whose body is not valid JSON at all causes the
        ``deserialize`` call to raise :class:`ProtocolError`, which is
        re-raised by the error-handling block (the generic
        ``TransportError`` is never reached)."""
        fake_resp = _FakeHTTPResponse(404, b"just some text")

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(ProtocolError):
                transport.send(_request_msg("ghost"), _endpoint("ghost"), timeout=5.0)

    def test_send_404_error_envelope_wrong_code_raises_transport_error(self) -> None:
        """A 404 whose Error envelope has an unrecognised code raises
        :class:`TransportError`."""
        request = _request_msg("ghost")
        error_envelope = Error.to(
            request,
            code="some_other_code",
            message="something else",
        )
        fake_resp = _FakeHTTPResponse(404, serialize(error_envelope).encode("utf-8"))

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            with pytest.raises(TransportError, match="HTTP 404"):
                transport.send(request, _endpoint("ghost"), timeout=5.0)

    def test_send_ignores_endpoint_routes_via_broker(self) -> None:
        """The *endpoint* argument is ignored — the transport always POSTs
        to the broker's ``/messages`` regardless of the endpoint fields."""
        request = _request_msg("agent-x")
        reply = Response.to(request, body={"ok": True})
        fake_resp = _FakeHTTPResponse(200, serialize(reply).encode("utf-8"))

        with _patch_http(response=fake_resp) as mock_conn_cls:
            transport = NetworkedBrokerTransport("broker.local", 7000)
            # Pass an endpoint with different host/port/path.
            weird_ep = Endpoint(
                agent_id="agent-x", host="other.host", port=1234, path="/custom"
            )
            transport.send(request, weird_ep, timeout=5.0)

        fake_conn = mock_conn_cls.return_value
        method, path, _body, _headers = fake_conn._request_args
        assert method == "POST"
        assert path == "/messages"  # always /messages, never /custom


# ===================================================================
# NetworkedBrokerTransport — health_check()
# ===================================================================


class TestNetworkedBrokerTransportHealthCheck:
    """Unit tests for :meth:`NetworkedBrokerTransport.health_check`."""

    def test_health_check_returns_true_on_200(self) -> None:
        """``GET /health`` returns 200 → ``True``."""
        fake_resp = _FakeHTTPResponse(200)

        with _patch_http(response=fake_resp) as mock_conn_cls:
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.health_check(_endpoint(), timeout=2.0)

        assert result is True
        fake_conn = mock_conn_cls.return_value
        method, path, _body, _headers = fake_conn._request_args
        assert method == "GET"
        assert path == "/health"

    def test_health_check_returns_false_on_oserror(self) -> None:
        """``OSError`` during request → ``False`` (no exception raised)."""
        with _patch_http(side_effect=OSError("connection refused")):
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.health_check(_endpoint(), timeout=2.0)

        assert result is False

    def test_health_check_returns_false_on_non_200(self) -> None:
        """A non-200 status (e.g. 503) returns ``False``."""
        fake_resp = _FakeHTTPResponse(503)

        with _patch_http(response=fake_resp):
            transport = NetworkedBrokerTransport("localhost", 8000)
            result = transport.health_check(_endpoint(), timeout=2.0)

        assert result is False


# ===================================================================
# NetworkedBrokerTransport — https scheme
# ===================================================================


class TestNetworkedBrokerTransportHttps:
    """Verify that the ``scheme`` parameter switches to ``HTTPSConnection``."""

    def test_https_scheme_uses_httpsconnection(self) -> None:
        """When ``scheme="https"`` the transport creates an
        ``HTTPSConnection`` instead of ``HTTPConnection``."""
        request = _request_msg("agent-a")
        reply = Response.to(request, body={"echo": "pong"})
        fake_resp = _FakeHTTPResponse(200, serialize(reply).encode("utf-8"))

        with patch(
            "robotsix_agent_comm.transport.brokered.http.client.HTTPSConnection",
            return_value=_FakeHTTPConnection(response=fake_resp),
        ) as mock_https:
            transport = NetworkedBrokerTransport("secure.local", 443, scheme="https")
            transport.send(request, _endpoint(), timeout=5.0)

        mock_https.assert_called_once_with("secure.local", 443, timeout=5.0)


# ===================================================================
# BrokeredRegistry — register()
# ===================================================================


class TestBrokeredRegistryRegister:
    """Unit tests for :meth:`BrokeredRegistry.register`."""

    def test_register_posts_to_agents_with_correct_json(self) -> None:
        """``register()`` POSTs to ``/agents`` with the endpoint fields
        serialised as JSON."""
        fake_resp = _FakeHTTPResponse(201)

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("broker.local", 7000)
            ep = Endpoint(
                agent_id="worker-1",
                host="10.0.0.5",
                port=9000,
                scheme="http",
                path="/messages",
            )
            registry.register(ep)

        fake_conn = mock_conn_cls.return_value
        method, path, body_bytes, headers = fake_conn._request_args
        assert method == "POST"
        assert path == "/agents"
        assert headers == {"Content-Type": "application/json"}

        body = json.loads(body_bytes.decode("utf-8"))
        assert body["agent_id"] == "worker-1"
        assert body["host"] == "10.0.0.5"
        assert body["port"] == 9000
        assert body["scheme"] == "http"
        assert body["path"] == "/messages"
        assert body["capabilities"] == {}

    def test_register_includes_capabilities_empty_dict(self) -> None:
        """The ``capabilities`` field is always included as an empty dict."""
        fake_resp = _FakeHTTPResponse(201)

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("localhost", 8000)
            registry.register(_endpoint("agent-x"))

        fake_conn = mock_conn_cls.return_value
        _method, _path, body_bytes, _headers = fake_conn._request_args
        body = json.loads(body_bytes.decode("utf-8"))
        assert body["capabilities"] == {}

    def test_register_does_not_return_value(self) -> None:
        """``register()`` returns ``None`` (fire-and-forget)."""
        fake_resp = _FakeHTTPResponse(201)

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result: object = registry.register(_endpoint("agent-x"))  # type: ignore[func-returns-value]

        assert result is None

    def test_register_default_scheme_and_path(self) -> None:
        """When the endpoint uses default scheme/path they are forwarded."""
        fake_resp = _FakeHTTPResponse(201)

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("localhost", 8000)
            ep = Endpoint(agent_id="a", host="h", port=1)
            registry.register(ep)

        fake_conn = mock_conn_cls.return_value
        _method, _path, body_bytes, _headers = fake_conn._request_args
        body = json.loads(body_bytes.decode("utf-8"))
        assert body["scheme"] == "http"
        assert body["path"] == "/messages"


# ===================================================================
# BrokeredRegistry — unregister()
# ===================================================================


class TestBrokeredRegistryUnregister:
    """Unit tests for :meth:`BrokeredRegistry.unregister`."""

    def test_unregister_sends_delete_to_agents_agent_id(self) -> None:
        """``unregister()`` sends ``DELETE /agents/{agent_id}``."""
        fake_resp = _FakeHTTPResponse(204)

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("localhost", 8000)
            registry.unregister("worker-7")

        fake_conn = mock_conn_cls.return_value
        method, path, body, headers = fake_conn._request_args
        assert method == "DELETE"
        assert path == "/agents/worker-7"
        assert body is None
        assert headers == {}  # no Content-Type when body is None

    def test_unregister_no_error_on_nonexistent(self) -> None:
        """``unregister()`` is idempotent — no error for unknown agents."""
        fake_resp = _FakeHTTPResponse(204)

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            # Must not raise.
            registry.unregister("phantom")

    def test_unregister_does_not_return_value(self) -> None:
        """``unregister()`` returns ``None``."""
        fake_resp = _FakeHTTPResponse(204)

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result: object = registry.unregister("agent-x")  # type: ignore[func-returns-value]

        assert result is None


# ===================================================================
# BrokeredRegistry — lookup()
# ===================================================================


class TestBrokeredRegistryLookup:
    """Unit tests for :meth:`BrokeredRegistry.lookup`."""

    def test_lookup_gets_agents_and_finds_target(self) -> None:
        """``lookup()`` GETs ``/agents`` and returns the matching
        :class:`Endpoint`."""
        agents_payload = {
            "agents": [
                {"agent_id": "agent-1", "host": "h1", "port": 1},
                {"agent_id": "agent-2", "host": "h2", "port": 2},
                {"agent_id": "agent-3", "host": "h3", "port": 3},
            ]
        }
        fake_resp = _FakeHTTPResponse(200, json.dumps(agents_payload).encode("utf-8"))

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("broker.local", 7000)
            ep = registry.lookup("agent-2")

        assert isinstance(ep, Endpoint)
        assert ep.agent_id == "agent-2"
        # Placeholder values (transport ignores them).
        assert ep.host == "broker"
        assert ep.port == 7000

    def test_lookup_raises_agent_not_found_when_absent(self) -> None:
        """When the agent list does not contain the requested ID,
        :class:`AgentNotFoundError` is raised."""
        agents_payload = {
            "agents": [
                {"agent_id": "agent-1", "host": "h1", "port": 1},
            ]
        }
        fake_resp = _FakeHTTPResponse(200, json.dumps(agents_payload).encode("utf-8"))

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(AgentNotFoundError, match="unknown agent"):
                registry.lookup("ghost")

    def test_lookup_gets_agents_with_get_method(self) -> None:
        """Verify that ``lookup()`` uses ``GET /agents``."""
        agents_payload = {"agents": [{"agent_id": "a", "host": "h", "port": 1}]}
        fake_resp = _FakeHTTPResponse(200, json.dumps(agents_payload).encode("utf-8"))

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("localhost", 8000)
            registry.lookup("a")

        fake_conn = mock_conn_cls.return_value
        method, path, _body, _headers = fake_conn._request_args
        assert method == "GET"
        assert path == "/agents"


# ===================================================================
# BrokeredRegistry — list_agents()
# ===================================================================


class TestBrokeredRegistryListAgents:
    """Unit tests for :meth:`BrokeredRegistry.list_agents`."""

    def test_list_agents_returns_all_endpoints(self) -> None:
        """``list_agents()`` returns a list of :class:`Endpoint` objects
        with correct agent IDs and placeholder host/port."""
        agents_payload = {
            "agents": [
                {"agent_id": "agent-1", "host": "h1", "port": 1},
                {"agent_id": "agent-2", "host": "h2", "port": 2},
            ]
        }
        fake_resp = _FakeHTTPResponse(200, json.dumps(agents_payload).encode("utf-8"))

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("broker.local", 7000)
            result = registry.list_agents()

        assert len(result) == 2
        assert all(isinstance(ep, Endpoint) for ep in result)
        assert result[0].agent_id == "agent-1"
        assert result[1].agent_id == "agent-2"
        assert result[0].host == "broker"
        assert result[0].port == 7000

    def test_list_agents_uses_get_method(self) -> None:
        """``list_agents()`` sends ``GET /agents``."""
        fake_resp = _FakeHTTPResponse(200, json.dumps({"agents": []}).encode("utf-8"))

        with _patch_http(response=fake_resp) as mock_conn_cls:
            registry = BrokeredRegistry("localhost", 8000)
            registry.list_agents()

        fake_conn = mock_conn_cls.return_value
        method, path, _body, _headers = fake_conn._request_args
        assert method == "GET"
        assert path == "/agents"

    def test_list_agents_empty_registry(self) -> None:
        """An empty ``agents`` list returns an empty Python list."""
        fake_resp = _FakeHTTPResponse(200, json.dumps({"agents": []}).encode("utf-8"))

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result = registry.list_agents()

        assert result == []

    def test_list_agents_handles_missing_agents_key(self) -> None:
        """When the JSON response lacks an ``agents`` key, return ``[]``."""
        fake_resp = _FakeHTTPResponse(200, b'{"other": "data"}')

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result = registry.list_agents()

        assert result == []

    def test_list_agents_skips_non_dict_entries(self) -> None:
        """Non-dict entries in the ``agents`` list are silently skipped."""
        agents_payload = {
            "agents": [
                {"agent_id": "valid", "host": "h", "port": 1},
                "not-a-dict",
                42,
                None,
            ]
        }
        fake_resp = _FakeHTTPResponse(200, json.dumps(agents_payload).encode("utf-8"))

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result = registry.list_agents()

        assert len(result) == 1
        assert result[0].agent_id == "valid"


# ===================================================================
# BrokeredRegistry — error handling
# ===================================================================


class TestBrokeredRegistryErrors:
    """Error-handling tests for :class:`BrokeredRegistry`."""

    def test_lookup_non_200_status_raises_transport_error(self) -> None:
        """When ``GET /agents`` returns a non-200 status, ``lookup()``
        raises :class:`TransportError`."""
        fake_resp = _FakeHTTPResponse(500, b"boom")

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(TransportError, match="HTTP 500"):
                registry.lookup("agent-x")

    def test_list_agents_non_200_status_raises_transport_error(self) -> None:
        """When ``GET /agents`` returns a non-200 status, ``list_agents()``
        raises :class:`TransportError`."""
        fake_resp = _FakeHTTPResponse(503, b"unavailable")

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(TransportError, match="HTTP 503"):
                registry.list_agents()

    def test_register_oserror_propagates(self) -> None:
        """``OSError`` during ``register()`` propagates to the caller."""
        with _patch_http(side_effect=OSError("connection refused")):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(OSError, match="connection refused"):
                registry.register(_endpoint("agent-x"))

    def test_lookup_oserror_propagates(self) -> None:
        """``OSError`` during ``lookup()`` propagates to the caller."""
        with _patch_http(side_effect=OSError("connection refused")):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(OSError, match="connection refused"):
                registry.lookup("agent-x")

    def test_unregister_oserror_propagates(self) -> None:
        """``OSError`` during ``unregister()`` propagates to the caller."""
        with _patch_http(side_effect=OSError("connection refused")):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(OSError, match="connection refused"):
                registry.unregister("agent-x")

    def test_list_agents_bad_json_returns_empty_list(self) -> None:
        """When the response body is not valid JSON, ``_request`` catches
        ``json.JSONDecodeError`` and stores the raw string.  ``list_agents``
        guards with ``isinstance(parsed, dict)`` so it falls back to an
        empty list."""
        fake_resp = _FakeHTTPResponse(200, b"not json at all")

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            result = registry.list_agents()

        assert result == []

    def test_lookup_bad_json_raises_agent_not_found(self) -> None:
        """When the response body is not valid JSON, ``_request`` stores
        the raw string.  ``lookup`` guards with ``isinstance(parsed, dict)``
        so the agent list is empty → :class:`AgentNotFoundError`."""
        fake_resp = _FakeHTTPResponse(200, b"garbage")

        with _patch_http(response=fake_resp):
            registry = BrokeredRegistry("localhost", 8000)
            with pytest.raises(AgentNotFoundError):
                registry.lookup("agent-x")

    def test_broker_url_property(self) -> None:
        """The ``broker_url`` property returns the correct origin."""
        registry = BrokeredRegistry("broker.local", 7000)
        assert registry.broker_url == "http://broker.local:7000"

        https_registry = BrokeredRegistry("secure.local", 443, scheme="https")
        assert https_registry.broker_url == "https://secure.local:443"

    def test_https_scheme_uses_httpsconnection(self) -> None:
        """When ``scheme="https"`` the registry uses ``HTTPSConnection``."""
        fake_resp = _FakeHTTPResponse(200, json.dumps({"agents": []}).encode("utf-8"))

        with patch(
            "robotsix_agent_comm.transport.brokered.http.client.HTTPSConnection",
            return_value=_FakeHTTPConnection(response=fake_resp),
        ) as mock_https:
            registry = BrokeredRegistry("secure.local", 443, scheme="https")
            registry.list_agents()

        mock_https.assert_called_once_with("secure.local", 443, timeout=5.0)


# ===================================================================
# create_transport_pair()
# ===================================================================


class TestCreateTransportPair:
    """Unit tests for :func:`create_transport_pair`."""

    def test_in_process_mode_returns_registry_and_transport_client(self) -> None:
        """``mode="in-process"`` returns ``(Registry, TransportClient)``."""
        reg, transport = create_transport_pair("in-process")

        assert isinstance(reg, Registry)
        assert isinstance(transport, TransportClient)

    def test_brokered_mode_returns_brokered_pair(self) -> None:
        """``mode="brokered"`` returns
        ``(BrokeredRegistry, NetworkedBrokerTransport)``."""
        reg, transport = create_transport_pair(
            "brokered", broker_host="bhost", broker_port=9999, broker_scheme="http"
        )

        assert isinstance(reg, BrokeredRegistry)
        assert isinstance(transport, NetworkedBrokerTransport)
        assert reg.broker_url == "http://bhost:9999"
        assert transport.broker_url == "http://bhost:9999"

    def test_brokered_mode_defaults(self) -> None:
        """Default host/port/scheme are passed through."""
        reg, transport = create_transport_pair("brokered")

        assert isinstance(reg, BrokeredRegistry)
        assert isinstance(transport, NetworkedBrokerTransport)
        assert reg.broker_url == "http://127.0.0.1:0"

    def test_unknown_mode_raises_value_error(self) -> None:
        """An unrecognised mode raises :class:`ValueError`."""
        with pytest.raises(ValueError, match="unknown transport mode"):
            create_transport_pair("garbage")

    def test_brokered_mode_passes_scheme(self) -> None:
        """The ``broker_scheme`` parameter is forwarded correctly."""
        reg, transport = create_transport_pair(
            "brokered", broker_host="h", broker_port=1, broker_scheme="https"
        )

        assert reg.broker_url == "https://h:1"  # type: ignore[union-attr]
        assert transport.broker_url == "https://h:1"  # type: ignore[attr-defined]
