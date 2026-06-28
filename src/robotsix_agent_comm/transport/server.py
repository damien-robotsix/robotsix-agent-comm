"""HTTP+JSON transport server.

Wraps :class:`http.server.ThreadingHTTPServer` so an agent can receive
protocol messages over HTTP using only the standard library. ``POST`` to
the message path deserializes the body, dispatches to a user callback,
and returns the serialized reply (or ``204`` for fire-and-forget).
``GET /health`` reports liveness.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from ..protocol import Message, ProtocolError, deserialize, serialize
from .endpoints import DEFAULT_MESSAGE_PATH, HEALTH_PATH

MessageHandler = Callable[[Message], Message | None]


class _MessageHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the transport dispatch state."""

    daemon_threads = True

    message_handler: MessageHandler
    message_path: str


class _BaseRequestHandler(BaseHTTPRequestHandler):
    """Shared mixin for request handlers that write JSON responses.

    Defines ``_write_json`` once so every handler in the codebase
    that inherits from ``BaseHTTPRequestHandler`` can use it.
    """

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _MessageRequestHandler(_BaseRequestHandler):
    """Routes ``POST <message-path>`` and ``GET /health``."""

    def _server(self) -> _MessageHTTPServer:
        return cast("_MessageHTTPServer", self.server)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        """Dispatch ``GET /health`` returning a liveness probe response."""
        if self.path == HEALTH_PATH:
            self._write_json(200, {"status": "ok"})
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        """Dispatch ``POST <message-path>`` to deserialize and route a message."""
        server = self._server()
        if self.path != server.message_path:
            self._write_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            message = deserialize(raw)
        except ProtocolError as exc:
            self._write_json(400, {"error": str(exc)})
            return

        reply = server.message_handler(message)
        if reply is None:
            self.send_response(204)
            self.end_headers()
            return
        body = serialize(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Silence the default stderr request logging."""


class TransportServer:
    """An agent-side HTTP listener for protocol messages."""

    def __init__(
        self,
        handler: MessageHandler,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        message_path: str = DEFAULT_MESSAGE_PATH,
    ) -> None:
        """Initialize the transport server with handler and network settings."""
        self._server = _MessageHTTPServer((host, port), _MessageRequestHandler)
        self._server.message_handler = handler
        self._server.message_path = message_path
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """Return the bound host address."""
        return cast("tuple[str, int]", self._server.server_address)[0]

    @property
    def port(self) -> int:
        """Return the actually-bound port (useful when ``port=0``)."""
        return cast("tuple[str, int]", self._server.server_address)[1]

    def start(self) -> None:
        """Serve requests on a background daemon thread."""
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop serving and release the socket."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def __enter__(self) -> TransportServer:
        """Enter the runtime context, starting the server."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the runtime context, stopping the server."""
        self.stop()
