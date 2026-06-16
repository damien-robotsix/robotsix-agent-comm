"""Unit tests for the broker request handler.

Follows the ``_make_handler`` mock pattern from
``tests/transport/test_server.py`` so the handler can be tested
without a running server.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from robotsix_agent_comm.broker.server import (
    BrokerServer,
    _BrokerHTTPServer,
    _BrokerRequestHandler,
)
from robotsix_agent_comm.protocol import (
    Error,
    Metadata,
    Notification,
    Request,
    Response,
    deserialize,
    serialize,
)
from robotsix_agent_comm.transport import AgentNotFoundError, Registry
from robotsix_agent_comm.transport.endpoints import HEALTH_PATH

# ---------------------------------------------------------------------------
# Helper: construct a handler bypassing BaseHTTPRequestHandler.__init__
# ---------------------------------------------------------------------------


def _make_handler(**kwargs: Any) -> Any:
    """Create a handler instance with mocked I/O and server state.

    Returns ``Any``: the instance deliberately has its I/O methods
    replaced by mocks, so callers access mock-only attributes
    (``assert_called_once_with``, etc.) that the real
    ``_BrokerRequestHandler`` type does not declare.
    """
    handler: Any = object.__new__(_BrokerRequestHandler)
    handler.path = kwargs.get("path", "/agents")
    handler.headers = kwargs.get("headers", MagicMock())
    handler.rfile = kwargs.get("rfile", MagicMock())
    handler.wfile = kwargs.get("wfile", MagicMock())
    handler.send_response = kwargs.get("send_response", MagicMock())
    handler.send_header = kwargs.get("send_header", MagicMock())
    handler.end_headers = kwargs.get("end_headers", MagicMock())

    server = kwargs.get("server")
    if server is None:
        import threading as _threading

        server = MagicMock(spec=_BrokerHTTPServer)
        server.registry = Registry()
        server.capabilities_lock = _threading.Lock()
        server.capabilities = {}
        server.router = MagicMock()
    handler.server = server

    return handler


def _body_written(handler: Any) -> dict[str, Any]:
    """Return the JSON payload written to the handler's ``wfile``."""
    call_args = handler.wfile.write.call_args
    if call_args is None:
        return {}
    raw = call_args[0][0]
    if isinstance(raw, bytes):
        result: dict[str, Any] = json.loads(raw)
    else:
        result = json.loads(raw.decode("utf-8"))
    return result


def _set_body(handler: Any, body: str) -> None:
    """Configure the handler's ``rfile`` and ``Content-Length`` header."""
    raw = body.encode("utf-8")
    handler.headers.get.return_value = str(len(raw))
    handler.rfile.read.return_value = raw


# ---------------------------------------------------------------------------
# _BrokerRequestHandler — GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_get_health_returns_200(self) -> None:
        handler = _make_handler(path=HEALTH_PATH)
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        assert _body_written(handler) == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /agents — registration
# ---------------------------------------------------------------------------


class TestRegisterEndpoint:
    def test_valid_new_agent_returns_201(self) -> None:
        handler = _make_handler()
        _set_body(
            handler,
            json.dumps(
                {
                    "agent_id": "agent-a",
                    "host": "127.0.0.1",
                    "port": 9000,
                    "capabilities": {"role": "worker"},
                }
            ),
        )
        handler.do_POST()

        handler.send_response.assert_called_once_with(201)
        assert _body_written(handler) == {"agent_id": "agent-a"}

        # Verify the endpoint was registered.
        server = handler.server
        endpoint = server.registry.lookup("agent-a")
        assert endpoint.agent_id == "agent-a"
        assert endpoint.host == "127.0.0.1"
        assert endpoint.port == 9000

    def test_duplicate_agent_id_returns_200(self) -> None:
        handler = _make_handler()
        payload = json.dumps({"agent_id": "agent-b", "host": "127.0.0.1", "port": 9001})
        _set_body(handler, payload)
        handler.do_POST()
        assert handler.send_response.call_args[0][0] == 201

        # Reset mocks and register again.
        handler.send_response.reset_mock()
        handler.wfile.write.reset_mock()
        handler.rfile.read.return_value = payload.encode("utf-8")
        handler.headers.get.return_value = str(len(payload.encode("utf-8")))
        handler.do_POST()

        handler.send_response.assert_called_once_with(200)
        assert _body_written(handler) == {"agent_id": "agent-b"}

    def test_missing_agent_id_returns_400(self) -> None:
        handler = _make_handler()
        _set_body(handler, json.dumps({"host": "127.0.0.1", "port": 9000}))
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        assert "agent_id" in _body_written(handler)["error"]

    def test_missing_host_returns_400(self) -> None:
        handler = _make_handler()
        _set_body(handler, json.dumps({"agent_id": "agent-c", "port": 9000}))
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        assert "host" in _body_written(handler)["error"]

    def test_missing_port_returns_400(self) -> None:
        handler = _make_handler()
        _set_body(handler, json.dumps({"agent_id": "agent-c", "host": "127.0.0.1"}))
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        assert "port" in _body_written(handler)["error"]

    def test_body_is_array_returns_400(self) -> None:
        handler = _make_handler()
        _set_body(handler, json.dumps([1, 2, 3]))
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        assert "object" in _body_written(handler)["error"].lower()

    def test_invalid_json_returns_400(self) -> None:
        handler = _make_handler()
        _set_body(handler, "{not valid json")
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        assert "JSON" in _body_written(handler)["error"]

    def test_optional_fields_defaulted(self) -> None:
        """When scheme and path are missing, defaults are applied."""
        handler = _make_handler()
        _set_body(
            handler,
            json.dumps({"agent_id": "agent-d", "host": "10.0.0.1", "port": 5000}),
        )
        handler.do_POST()

        endpoint = handler.server.registry.lookup("agent-d")
        assert endpoint.scheme == "http"
        assert endpoint.path == "/messages"


# ---------------------------------------------------------------------------
# DELETE /agents/{id} — deregistration
# ---------------------------------------------------------------------------


class TestDeregisterEndpoint:
    def test_delete_existing_agent_returns_204(self) -> None:
        handler = _make_handler()
        # Register first.
        _set_body(
            handler,
            json.dumps({"agent_id": "agent-x", "host": "127.0.0.1", "port": 8000}),
        )
        handler.do_POST()

        # Now deregister.
        handler.send_response.reset_mock()
        handler.path = "/agents/agent-x"
        handler.do_DELETE()

        handler.send_response.assert_called_once_with(204)
        # Verify agent is removed.
        with pytest.raises(AgentNotFoundError):
            handler.server.registry.lookup("agent-x")

    def test_delete_nonexistent_agent_returns_204(self) -> None:
        handler = _make_handler()
        handler.path = "/agents/ghost"
        handler.do_DELETE()

        handler.send_response.assert_called_once_with(204)

    def test_delete_missing_agent_id_returns_400(self) -> None:
        handler = _make_handler()
        handler.path = "/agents/"
        handler.do_DELETE()

        handler.send_response.assert_called_once_with(400)
        assert "agent_id" in _body_written(handler)["error"]


# ---------------------------------------------------------------------------
# GET /agents — discovery
# ---------------------------------------------------------------------------


class TestDiscoveryEndpoint:
    def test_empty_registry_returns_empty_list(self) -> None:
        handler = _make_handler()
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        assert _body_written(handler) == {"agents": []}

    def test_after_registrations_returns_all_with_capabilities(self) -> None:
        handler = _make_handler()

        # Register two agents.
        for agent_id, port, caps in [
            ("agent-1", 9001, {"role": "worker"}),
            ("agent-2", 9002, {"role": "dispatcher", "zone": "east"}),
        ]:
            _set_body(
                handler,
                json.dumps(
                    {
                        "agent_id": agent_id,
                        "host": "127.0.0.1",
                        "port": port,
                        "capabilities": caps,
                    }
                ),
            )
            handler.do_POST()
            # Reset mocks for next POST
            handler.send_response.reset_mock()
            handler.wfile.write.reset_mock()

        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        result = _body_written(handler)
        agents = result["agents"]
        assert len(agents) == 2

        # Build lookup by agent_id.
        by_id = {a["agent_id"]: a["capabilities"] for a in agents}
        assert by_id["agent-1"] == {"role": "worker"}
        assert by_id["agent-2"] == {"role": "dispatcher", "zone": "east"}


# ---------------------------------------------------------------------------
# POST /messages — send
# ---------------------------------------------------------------------------


def _server_with_router(router_mock: Any) -> Any:
    """Build a mock server that carries a stubbed router."""
    import threading as _threading

    server = MagicMock(spec=_BrokerHTTPServer)
    server.registry = Registry()
    server.capabilities_lock = _threading.Lock()
    server.capabilities = {}
    server.router = router_mock
    return server


class TestSendEndpoint:
    def test_valid_request_routes_and_returns_200(self) -> None:
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        raw = serialize(request)

        router = MagicMock()
        router.route.return_value = None  # will be set per test

        server = _server_with_router(router)
        # Pre-register the recipient so lookup succeeds.
        from robotsix_agent_comm.transport.endpoints import Endpoint

        server.registry.register(
            Endpoint(agent_id="agent-b", host="127.0.0.1", port=9001)
        )

        expected_reply = Response.to(
            request,
            body={"echo": "pong"},
            sender="agent-b",
        )
        router.route.return_value = expected_reply

        handler = _make_handler(server=server, path="/messages")
        _set_body(handler, raw)
        handler.do_POST()

        router.route.assert_called_once()
        handler.send_response.assert_called_once_with(200)
        reply = deserialize(json.dumps(_body_written(handler)))
        assert reply.body == {"echo": "pong"}

    def test_notification_returns_204(self) -> None:
        note = Notification(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"event": "tick"},
        )
        raw = serialize(note)

        router = MagicMock()
        router.route.return_value = None

        from robotsix_agent_comm.transport.endpoints import Endpoint

        server = _server_with_router(router)
        server.registry.register(
            Endpoint(agent_id="agent-b", host="127.0.0.1", port=9001)
        )

        handler = _make_handler(server=server, path="/messages")
        _set_body(handler, raw)
        handler.do_POST()

        router.route.assert_called_once()
        handler.send_response.assert_called_once_with(204)

    def test_unknown_recipient_returns_404_with_error_envelope(self) -> None:
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="ghost"),
            body={},
        )
        raw = serialize(request)

        handler = _make_handler(path="/messages")
        _set_body(handler, raw)
        handler.do_POST()

        handler.send_response.assert_called_once_with(404)
        error_msg = deserialize(json.dumps(_body_written(handler)))
        assert isinstance(error_msg, Error)
        assert error_msg.body.get("code") == "unknown_recipient"

    def test_empty_recipient_returns_400(self) -> None:
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient=""),
            body={},
        )
        raw = serialize(request)

        handler = _make_handler(path="/messages")
        _set_body(handler, raw)
        handler.do_POST()

        handler.send_response.assert_called_once_with(400)
        assert "recipient" in _body_written(handler)["error"].lower()

    def test_malformed_json_body_returns_400(self) -> None:
        handler = _make_handler(path="/messages")
        _set_body(handler, "{not valid")
        handler.do_POST()

        handler.send_response.assert_called_once_with(400)
        assert "error" in _body_written(handler)

    def test_unreachable_recipient_returns_502(self) -> None:
        """When router raises DeliveryError, broker returns 502."""
        from robotsix_agent_comm.transport.errors import DeliveryError

        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={},
        )
        raw = serialize(request)

        router = MagicMock()
        router.route.side_effect = DeliveryError("connection refused")

        server = _server_with_router(router)
        from robotsix_agent_comm.transport.endpoints import Endpoint

        server.registry.register(Endpoint(agent_id="agent-b", host="127.0.0.1", port=1))

        handler = _make_handler(server=server, path="/messages")
        _set_body(handler, raw)
        handler.do_POST()

        handler.send_response.assert_called_once_with(502)
        error_msg = deserialize(json.dumps(_body_written(handler)))
        assert isinstance(error_msg, Error)
        assert error_msg.body.get("code") == "delivery_failed"


# ---------------------------------------------------------------------------
# Unknown paths
# ---------------------------------------------------------------------------


class TestUnknownPaths:
    def test_get_unknown_returns_404(self) -> None:
        handler = _make_handler(path="/unknown")
        handler.do_GET()
        handler.send_response.assert_called_once_with(404)
        assert _body_written(handler) == {"error": "not found"}

    def test_post_unknown_returns_404(self) -> None:
        handler = _make_handler(path="/unknown")
        handler.do_POST()
        handler.send_response.assert_called_once_with(404)
        assert _body_written(handler) == {"error": "not found"}


# ---------------------------------------------------------------------------
# BrokerServer lifecycle and properties
# ---------------------------------------------------------------------------
# BrokerServer lifecycle and properties
# ---------------------------------------------------------------------------


class TestBrokerServerLifecycle:
    def test_start_creates_daemon_thread(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()  # release real socket
        bs._server = mock_http

        with patch("threading.Thread") as mock_thread_cls:
            bs.start()

        mock_thread_cls.assert_called_once()
        _, kwargs = mock_thread_cls.call_args
        assert kwargs["target"] is mock_http.serve_forever
        assert kwargs["daemon"] is True
        mock_thread_cls.return_value.start.assert_called_once()

    def test_start_idempotent(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()
        bs._server = mock_http

        with patch("threading.Thread") as mock_thread_cls:
            bs.start()
            bs.start()

        mock_thread_cls.assert_called_once()

    def test_stop_shuts_down_and_joins(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()
        bs._server = mock_http

        mock_thread = MagicMock(spec=threading.Thread)
        bs._thread = mock_thread

        bs.stop()

        mock_http.shutdown.assert_called_once()
        mock_http.server_close.assert_called_once()
        mock_thread.join.assert_called_once()
        assert bs._thread is None

    def test_stop_when_not_started_is_safe(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()
        bs._server = mock_http

        bs.stop()

        mock_http.shutdown.assert_called_once()
        mock_http.server_close.assert_called_once()

    def test_close_is_alias_for_stop(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()
        bs._server = mock_http

        mock_thread = MagicMock(spec=threading.Thread)
        bs._thread = mock_thread

        bs.close()

        mock_http.shutdown.assert_called_once()
        mock_http.server_close.assert_called_once()
        mock_thread.join.assert_called_once()
        assert bs._thread is None

    def test_context_manager_starts_and_stops(self) -> None:
        mock_http = MagicMock(spec=_BrokerHTTPServer)
        bs = BrokerServer()
        bs._server.server_close()
        bs._server = mock_http

        with (
            patch.object(bs, "start") as mock_start,
            patch.object(bs, "stop") as mock_stop,
        ):
            with bs as entered:
                assert entered is bs
                mock_start.assert_called_once()
                mock_stop.assert_not_called()
            mock_stop.assert_called_once()


class TestBrokerServerProperties:
    def test_host_and_port_after_binding(self) -> None:
        bs = BrokerServer(host="127.0.0.1", port=0)
        try:
            assert bs.host == "127.0.0.1"
            assert bs.port > 0
        finally:
            bs._server.server_close()
