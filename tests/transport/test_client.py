"""Unit tests for TransportClient HTTP+JSON transport.

Covers error paths (timeout, connection failure, bad HTTP status,
malformed JSON) and protocol-level decisions (HTTPS branch, 204/empty
handling, health-check behaviour) without a running server.
"""

from __future__ import annotations

import http.client
from unittest.mock import MagicMock, patch

import pytest

from robotsix_agent_comm.protocol import (
    Metadata,
    Notification,
    ProtocolError,
    serialize,
)
from robotsix_agent_comm.transport.client import TransportClient
from robotsix_agent_comm.transport.endpoints import HEALTH_PATH, Endpoint
from robotsix_agent_comm.transport.errors import TransportError, TransportTimeoutError


@pytest.fixture
def client() -> TransportClient:
    return TransportClient()


@pytest.fixture
def endpoint() -> Endpoint:
    return Endpoint(agent_id="agent-b", host="127.0.0.1", port=9000)


@pytest.fixture
def message() -> Notification:
    return Notification(
        metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
        body={"action": "ping"},
    )


# -- send() tests -----------------------------------------------------------


def test_send_returns_deserialized_response(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """Happy path: POST returns 200 with a valid JSON response body."""
    reply = Notification(
        metadata=Metadata.create(sender="agent-b", recipient="agent-a"),
        body={"status": "ok"},
    )
    response_data = serialize(reply).encode("utf-8")

    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = response_data
    mock_conn.getresponse.return_value = mock_response

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.send(message, endpoint, timeout=5.0)

    assert result is not None
    assert result.body == {"status": "ok"}
    mock_conn.request.assert_called_once()
    mock_conn.close.assert_called_once()


def test_send_204_returns_none(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """204 No Content returns None (fire-and-forget / notification)."""
    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 204
    mock_response.read.return_value = b""
    mock_conn.getresponse.return_value = mock_response

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.send(message, endpoint, timeout=5.0)

    assert result is None
    mock_conn.close.assert_called_once()


def test_send_empty_body_returns_none(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """200 with empty body returns None."""
    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b""
    mock_conn.getresponse.return_value = mock_response

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.send(message, endpoint, timeout=5.0)

    assert result is None
    mock_conn.close.assert_called_once()


def test_send_timeout_raises_transport_timeout_error(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """TimeoutError is caught and re-raised as TransportTimeoutError."""
    mock_conn = MagicMock()
    mock_conn.request.side_effect = TimeoutError("timed out")

    with (
        patch.object(http.client, "HTTPConnection", return_value=mock_conn),
        pytest.raises(TransportTimeoutError) as excinfo,
    ):
        client.send(message, endpoint, timeout=5.0)

    assert "timed out after 5.0s" in str(excinfo.value)
    mock_conn.close.assert_called_once()


def test_send_oserror_raises_transport_error(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """OSError (e.g. connection refused) is caught and re-raised as TransportError."""
    mock_conn = MagicMock()
    mock_conn.request.side_effect = OSError("connection refused")

    with (
        patch.object(http.client, "HTTPConnection", return_value=mock_conn),
        pytest.raises(TransportError) as excinfo,
    ):
        client.send(message, endpoint, timeout=5.0)

    assert "failed to reach" in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)
    mock_conn.close.assert_called_once()


def test_send_http_400_raises_transport_error(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """HTTP status >= 400 raises TransportError."""
    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.read.return_value = b"Internal Server Error"
    mock_conn.getresponse.return_value = mock_response

    with (
        patch.object(http.client, "HTTPConnection", return_value=mock_conn),
        pytest.raises(TransportError) as excinfo,
    ):
        client.send(message, endpoint, timeout=5.0)

    assert "500" in str(excinfo.value)
    mock_conn.close.assert_called_once()


def test_send_malformed_response_raises_transport_error(
    client: TransportClient, endpoint: Endpoint, message: Notification
) -> None:
    """Malformed JSON in a 200 response raises TransportError wrapping ProtocolError."""
    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b"not json"
    mock_conn.getresponse.return_value = mock_response

    with (
        patch.object(http.client, "HTTPConnection", return_value=mock_conn),
        pytest.raises(TransportError) as excinfo,
    ):
        client.send(message, endpoint, timeout=5.0)

    assert "invalid response" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ProtocolError)
    mock_conn.close.assert_called_once()


def test_send_uses_https_connection_when_scheme_is_https(
    client: TransportClient, message: Notification
) -> None:
    """When endpoint.scheme == 'https', an HTTPSConnection is created."""
    ep = Endpoint(agent_id="agent-b", host="127.0.0.1", port=443, scheme="https")

    reply = Notification(
        metadata=Metadata.create(sender="agent-b", recipient="agent-a"),
        body={"status": "ok"},
    )
    response_data = serialize(reply).encode("utf-8")

    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = response_data
    mock_conn.getresponse.return_value = mock_response

    with patch.object(
        http.client, "HTTPSConnection", return_value=mock_conn
    ) as mock_https:
        client.send(message, ep, timeout=5.0)

    mock_https.assert_called_once_with("127.0.0.1", 443, timeout=5.0)
    mock_conn.close.assert_called_once()


# -- health_check() tests ---------------------------------------------------


def test_health_check_returns_true_on_200(
    client: TransportClient, endpoint: Endpoint
) -> None:
    """GET /health 200 → True."""
    mock_conn = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b""
    mock_conn.getresponse.return_value = mock_response

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.health_check(endpoint, timeout=5.0)

    assert result is True
    mock_conn.request.assert_called_once_with("GET", HEALTH_PATH)
    mock_conn.close.assert_called_once()


def test_health_check_returns_false_on_oserror(
    client: TransportClient, endpoint: Endpoint
) -> None:
    """OSError during health check returns False rather than raising."""
    mock_conn = MagicMock()
    mock_conn.request.side_effect = OSError("connection refused")

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.health_check(endpoint, timeout=5.0)

    assert result is False
    mock_conn.close.assert_called_once()


def test_health_check_timeout_returns_false(
    client: TransportClient, endpoint: Endpoint
) -> None:
    """TimeoutError (an OSError subclass) is caught and returns False."""
    mock_conn = MagicMock()
    mock_conn.request.side_effect = TimeoutError("timed out")

    with patch.object(http.client, "HTTPConnection", return_value=mock_conn):
        result = client.health_check(endpoint, timeout=5.0)

    assert result is False
    mock_conn.close.assert_called_once()
