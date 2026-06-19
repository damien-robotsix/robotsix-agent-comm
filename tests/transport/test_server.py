"""Unit tests for TransportServer and _MessageRequestHandler.

Covers HTTP handler dispatch, lifecycle management, properties,
and error handling without requiring a running server (except
for ephemeral-port property tests).
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

from robotsix_agent_comm.protocol import (
    Message,
    Metadata,
    Notification,
    Request,
    Response,
    deserialize,
    serialize,
)
from robotsix_agent_comm.transport.endpoints import DEFAULT_MESSAGE_PATH, HEALTH_PATH
from robotsix_agent_comm.transport.server import (
    MessageHandler,
    TransportServer,
    _MessageHTTPServer,
    _MessageRequestHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(**kwargs: Any) -> Any:
    """Create a handler instance bypassing BaseHTTPRequestHandler.__init__.

    Returns ``Any``: the instance deliberately has its I/O methods
    (``send_response``, ``wfile``, ...) replaced by mocks, so callers
    access mock-only attributes (``assert_called_once_with``, ``call_args``)
    that the real ``_MessageRequestHandler`` type does not declare.
    """
    handler: Any = object.__new__(_MessageRequestHandler)
    handler.path = kwargs.get("path", DEFAULT_MESSAGE_PATH)
    handler.headers = kwargs.get("headers", MagicMock())
    handler.rfile = kwargs.get("rfile", MagicMock())
    handler.wfile = kwargs.get("wfile", MagicMock())
    handler.send_response = kwargs.get("send_response", MagicMock())
    handler.send_header = kwargs.get("send_header", MagicMock())
    handler.end_headers = kwargs.get("end_headers", MagicMock())

    server = kwargs.get("server", MagicMock(spec=_MessageHTTPServer))
    handler.server = server

    # Only set server attributes when the caller did not supply the server
    # (i.e. we created the default mock).  Otherwise trust the caller's setup.
    if "server" not in kwargs:
        server.message_handler = kwargs.get("message_handler", MagicMock())
        server.message_path = kwargs.get("message_path", DEFAULT_MESSAGE_PATH)

    return handler


def _echo_handler() -> MessageHandler:
    def handler(message: Message) -> Message | None:
        if isinstance(message, Request):
            return Response.to(message, body={"echo": message.body})
        return None

    return handler


# ---------------------------------------------------------------------------
# _MessageRequestHandler
# ---------------------------------------------------------------------------


class TestMessageRequestHandler:
    def test_do_get_health_returns_200(self) -> None:
        handler = _make_handler(path=HEALTH_PATH)
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json")
        handler.end_headers.assert_called_once()
        body_written = handler.wfile.write.call_args[0][0]
        assert json.loads(body_written) == {"status": "ok"}

    def test_do_get_unknown_path_returns_404(self) -> None:
        handler = _make_handler(path="/unknown")
        handler.do_GET()

        handler.send_response.assert_called_once_with(404)
        handler.end_headers.assert_called_once()
        body_written = handler.wfile.write.call_args[0][0]
        assert json.loads(body_written) == {"error": "not found"}

    def test_do_post_wrong_path_returns_404(self) -> None:
        handler = _make_handler(path="/wrong")
        handler.do_POST()

        handler.send_response.assert_called_once_with(404)
        body_written = handler.wfile.write.call_args[0][0]
        assert json.loads(body_written) == {"error": "not found"}

    def test_do_post_valid_message_dispatches_and_returns_reply(self) -> None:
        server = MagicMock(spec=_MessageHTTPServer)
        server.message_handler = _echo_handler()
        server.message_path = DEFAULT_MESSAGE_PATH

        request_msg = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        raw = serialize(request_msg).encode("utf-8")

        headers = MagicMock()
        headers.get.return_value = str(len(raw))
        rfile = MagicMock()
        rfile.read.return_value = raw

        handler = _make_handler(server=server, headers=headers, rfile=rfile)
        handler.do_POST()

        handler.send_response.assert_called_once_with(200)
        body_written = handler.wfile.write.call_args[0][0]
        reply = deserialize(body_written.decode("utf-8"))
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}

    def test_do_post_handler_returns_none_sends_204(self) -> None:
        server = MagicMock(spec=_MessageHTTPServer)
        server.message_handler = lambda msg: None
        server.message_path = DEFAULT_MESSAGE_PATH

        note = Notification(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"event": "tick"},
        )
        raw = serialize(note).encode("utf-8")

        headers = MagicMock()
        headers.get.return_value = str(len(raw))
        rfile = MagicMock()
        rfile.read.return_value = raw

        handler = _make_handler(server=server, headers=headers, rfile=rfile)
        handler.do_POST()

        handler.send_response.assert_called_once_with(204)

    def test_do_post_protocol_error_during_deserialize_returns_400(self) -> None:
        """A valid JSON document that is not a valid protocol message
        raises ProtocolError → 400 response."""
        # Valid JSON, but missing required envelope fields → ProtocolError
        bad_json = json.dumps({"not": "a valid envelope"}).encode("utf-8")

        headers = MagicMock()
        headers.get.return_value = str(len(bad_json))
        rfile = MagicMock()
        rfile.read.return_value = bad_json

        handler = _make_handler(headers=headers, rfile=rfile)
        handler.do_POST()

        handler.send_response.assert_called_once_with(400)
        body_written = handler.wfile.write.call_args[0][0]
        body = json.loads(body_written)
        assert "error" in body
        # ProtocolError message mentions missing envelope fields
        assert "missing" in body["error"]

    def test_do_post_empty_body_is_handled(self) -> None:
        """Empty POST body raises ProtocolError during deserialize."""
        headers = MagicMock()
        headers.get.return_value = "0"
        rfile = MagicMock()
        rfile.read.return_value = b""

        handler = _make_handler(headers=headers, rfile=rfile)
        handler.do_POST()

        handler.send_response.assert_called_once_with(400)
        body_written = handler.wfile.write.call_args[0][0]
        body = json.loads(body_written)
        assert "error" in body

    def test_log_message_is_silent(self) -> None:
        handler = _make_handler()
        # Should not raise and should not produce any output
        handler.log_message("GET %s", "/health")
        # No assertions needed — no side effects by design

    def test_message_path_customization(self) -> None:
        """When message_path is customized, POST to default path → 404."""
        custom_path = "/custom-msgs"
        server = MagicMock(spec=_MessageHTTPServer)
        server.message_handler = lambda msg: None
        server.message_path = custom_path

        handler = _make_handler(server=server)
        # Default path should 404
        handler.path = DEFAULT_MESSAGE_PATH
        handler.do_POST()
        handler.send_response.assert_called_with(404)

        # Custom path should dispatch
        handler.send_response.reset_mock()
        note = Notification(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"event": "tick"},
        )
        raw = serialize(note).encode("utf-8")
        handler.headers.get.return_value = str(len(raw))
        handler.rfile.read.return_value = raw
        handler.path = custom_path
        handler.do_POST()
        handler.send_response.assert_called_once_with(204)


# ---------------------------------------------------------------------------
# TransportServer lifecycle and properties
# ---------------------------------------------------------------------------


class TestTransportServerLifecycle:
    def test_start_creates_daemon_thread_and_starts_serving(self) -> None:
        """start() creates a daemon thread targeting serve_forever."""
        server = MagicMock(spec=_MessageHTTPServer)
        ts = TransportServer(MagicMock())
        ts._server.server_close()  # release the real socket
        ts._server = server

        with patch.object(threading, "Thread") as mock_thread_cls:
            ts.start()

        mock_thread_cls.assert_called_once()
        _, kwargs = mock_thread_cls.call_args
        assert kwargs["target"] is server.serve_forever
        assert kwargs["daemon"] is True

        # The thread instance should have been started
        thread_instance = mock_thread_cls.return_value
        thread_instance.start.assert_called_once()

    def test_start_idempotent(self) -> None:
        """Calling start() twice only creates one thread."""
        server = MagicMock(spec=_MessageHTTPServer)
        ts = TransportServer(MagicMock())
        ts._server.server_close()
        ts._server = server

        with patch.object(threading, "Thread") as mock_thread_cls:
            ts.start()
            ts.start()

        mock_thread_cls.assert_called_once()

    def test_stop_shuts_down_closes_and_joins(self) -> None:
        """stop() calls shutdown, server_close, and thread.join()."""
        server = MagicMock(spec=_MessageHTTPServer)
        ts = TransportServer(MagicMock())
        ts._server.server_close()
        ts._server = server

        mock_thread = MagicMock(spec=threading.Thread)
        ts._thread = mock_thread

        ts.stop()

        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        mock_thread.join.assert_called_once()
        assert ts._thread is None

    def test_stop_when_not_started_is_safe(self) -> None:
        """stop() on a never-started server does not call join."""
        server = MagicMock(spec=_MessageHTTPServer)
        ts = TransportServer(MagicMock())
        ts._server.server_close()
        ts._server = server

        ts.stop()

        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        # No thread to join — _thread is None, nothing to assert

    def test_context_manager_starts_and_stops(self) -> None:
        """with block calls start() on enter and stop() on exit."""
        server = MagicMock(spec=_MessageHTTPServer)
        ts = TransportServer(MagicMock())
        ts._server.server_close()
        ts._server = server

        with (
            patch.object(ts, "start") as mock_start,
            patch.object(ts, "stop") as mock_stop,
        ):
            with ts as entered:
                assert entered is ts
                mock_start.assert_called_once()
                mock_stop.assert_not_called()

            mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# TransportServer properties (require a real bound server)
# ---------------------------------------------------------------------------


class TestTransportServerProperties:
    def test_host_and_port_after_binding(self) -> None:
        ts = TransportServer(_echo_handler(), host="127.0.0.1", port=0)
        try:
            assert ts.host == "127.0.0.1"
            # port=0 assigns an ephemeral port; the OS picks one > 0
            assert ts.port > 0
        finally:
            ts._server.server_close()

    def test_message_path_default_and_custom(self) -> None:
        ts_custom = TransportServer(_echo_handler(), message_path="/custom")
        try:
            assert ts_custom._server.message_path == "/custom"
        finally:
            ts_custom._server.server_close()

        ts_default = TransportServer(_echo_handler())
        try:
            assert ts_default._server.message_path == DEFAULT_MESSAGE_PATH
        finally:
            ts_default._server.server_close()
