"""HTTP+JSON transport client.

Sends serialized protocol messages over HTTP using only the standard
library (:mod:`http.client`), honouring ADR 0001 (stdlib-first) and
ADR 0005 (HTTP+JSON transport). Per-request timeouts are enforced and
socket/HTTP failures are surfaced as :class:`TransportError` subclasses.
"""

from __future__ import annotations

import http.client

from ..protocol import Message, ProtocolError, deserialize, serialize
from .base import Transport
from .endpoints import HEALTH_PATH, Endpoint
from .errors import TransportError, TransportTimeoutError

_JSON_HEADERS = {"Content-Type": "application/json"}


class TransportClient(Transport):
    """Delivers messages to remote endpoints over HTTP+JSON."""

    def _connect(
        self, endpoint: Endpoint, timeout: float
    ) -> http.client.HTTPConnection:
        if endpoint.scheme == "https":
            return http.client.HTTPSConnection(
                endpoint.host, endpoint.port, timeout=timeout
            )
        return http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)

    def send(
        self, message: Message, endpoint: Endpoint, *, timeout: float
    ) -> Message | None:
        """POST ``message`` to ``endpoint`` and return the deserialized reply.

        Returns ``None`` when the server replies ``204``/empty (e.g. a
        notification). Raises :class:`TransportTimeoutError` on timeout and
        :class:`TransportError` for other socket/HTTP failures.
        """
        body = serialize(message).encode("utf-8")
        conn = self._connect(endpoint, timeout)
        try:
            conn.request("POST", endpoint.path, body=body, headers=_JSON_HEADERS)
            response = conn.getresponse()
            status = response.status
            data = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"request to {endpoint.url} timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise TransportError(f"failed to reach {endpoint.url}: {exc}") from exc
        finally:
            conn.close()

        if status >= 400:
            raise TransportError(f"{endpoint.url} returned HTTP {status}: {data}")
        if status == 204 or not data:
            return None
        try:
            return deserialize(data)
        except ProtocolError as exc:
            raise TransportError(
                f"invalid response from {endpoint.url}: {exc}"
            ) from exc

    def health_check(self, endpoint: Endpoint, *, timeout: float) -> bool:
        """Return ``True`` if ``GET /health`` returns ``200``.

        Returns ``False`` when the endpoint is unreachable rather than
        raising, so callers can poll liveness cheaply.
        """
        conn = self._connect(endpoint, timeout)
        try:
            conn.request("GET", HEALTH_PATH)
            response = conn.getresponse()
            response.read()
            return response.status == 200
        except OSError:
            return False
        finally:
            conn.close()
