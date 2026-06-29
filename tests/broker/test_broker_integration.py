"""Integration tests for the broker server with real servers.

Follows the pattern from ``tests/transport/test_server_client.py``:
ephemeral-port servers, no mocks, real HTTP calls.
"""

from __future__ import annotations

import http.client
import json
import ssl
import time
from collections.abc import Iterator
from typing import Any

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import (
    Error,
    Message,
    Metadata,
    Notification,
    Request,
    Response,
    deserialize,
    serialize,
)
from robotsix_agent_comm.transport import UNKNOWN_RECIPIENT, TransportServer


def _json_request(
    method: str,
    broker: BrokerServer,
    path: str,
    body: dict[str, object] | list[object] | str | None = None,
    *,
    headers: dict[str, str] | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[int, Any]:
    """Make a raw HTTP request to the broker, return (status, parsed_body)."""
    conn: http.client.HTTPConnection
    if ssl_context is not None:
        conn = http.client.HTTPSConnection(
            broker.host, broker.port, timeout=5.0, context=ssl_context
        )
    else:
        conn = http.client.HTTPConnection(broker.host, broker.port, timeout=5.0)
    try:
        if isinstance(body, str):
            payload = body.encode("utf-8")
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        else:
            payload = None

        req_headers: dict[str, str] = {}
        if payload:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        conn.request(
            method,
            path,
            body=payload,
            headers=req_headers,
        )
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        status = resp.status
    finally:
        conn.close()

    parsed = None
    if data:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = data
    return status, parsed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBrokerIntegration:
    def test_register_and_discover(self, broker: BrokerServer) -> None:
        status, body = _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-1",
                "host": "127.0.0.1",
                "port": 9000,
                "capabilities": {"role": "worker"},
            },
        )
        assert status == 201
        assert body == {"agent_id": "agent-1"}

        # Discovery returns the agent with capabilities.
        status, body = _json_request("GET", broker, "/agents")
        assert status == 200
        assert isinstance(body, dict)
        agents = body.get("agents", [])
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "agent-1"
        assert agents[0]["capabilities"] == {"role": "worker"}

    def test_duplicate_register_returns_200(self, broker: BrokerServer) -> None:
        payload = {"agent_id": "agent-2", "host": "127.0.0.1", "port": 9001}
        status, _ = _json_request("POST", broker, "/agents", payload)
        assert status == 201

        status, _ = _json_request("POST", broker, "/agents", payload)
        assert status == 200

    def test_deregister_is_idempotent(self, broker: BrokerServer) -> None:
        # Register and deregister.
        _json_request(
            "POST",
            broker,
            "/agents",
            {"agent_id": "agent-3", "host": "127.0.0.1", "port": 9002},
        )

        status, _ = _json_request("DELETE", broker, "/agents/agent-3")
        assert status == 204

        # Deregister again — still 204.
        status, _ = _json_request("DELETE", broker, "/agents/agent-3")
        assert status == 204

        # Discovery is empty.
        status, body = _json_request("GET", broker, "/agents")
        assert status == 200
        assert body == {"agents": []}

    def test_send_request_routes_to_recipient(
        self, broker: BrokerServer, agent_server: tuple[TransportServer, list[Message]]
    ) -> None:
        agent_srv, received = agent_server

        # Register the agent server with the broker.
        status, _ = _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
                "capabilities": {"role": "echo"},
            },
        )
        assert status == 201

        # Verify discovery.
        status, body = _json_request("GET", broker, "/agents")
        assert status == 200
        assert isinstance(body, dict)
        agents = body.get("agents", [])
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "agent-b"
        assert agents[0]["capabilities"] == {"role": "echo"}

        # Send a Request via the broker.
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request("POST", broker, "/messages", serialize(request))
        assert status == 200

        # The response should be a serialised Response.
        assert isinstance(body, dict)
        reply = deserialize(json.dumps(body))
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}
        assert reply.correlation_id == request.message_id

        # The recipient received the request.
        assert len(received) == 1
        assert received[0].body == {"action": "ping"}

    def test_send_notification_forwards_and_returns_204(
        self, broker: BrokerServer, agent_server: tuple[TransportServer, list[Message]]
    ) -> None:
        agent_srv, received = agent_server

        _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
        )

        note = Notification(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"event": "tick"},
        )
        status, body = _json_request("POST", broker, "/messages", serialize(note))
        assert status == 204
        assert body is None

        assert len(received) == 1
        assert received[0].body == {"event": "tick"}

    def test_send_to_deregistered_agent_returns_404(
        self, broker: BrokerServer, agent_server: tuple[TransportServer, list[Message]]
    ) -> None:
        agent_srv, _ = agent_server

        _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
        )

        # Deregister.
        _json_request("DELETE", broker, "/agents/agent-b")

        # Now send — expect 404 with Error envelope.
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request("POST", broker, "/messages", serialize(request))
        assert status == 404
        assert isinstance(body, dict)
        error_msg = deserialize(json.dumps(body))
        assert isinstance(error_msg, Error)
        assert error_msg.body.get("code") == UNKNOWN_RECIPIENT

    def test_health_endpoint(self, broker: BrokerServer) -> None:
        status, body = _json_request("GET", broker, "/health")
        assert status == 200
        assert body == {"status": "ok"}

    def test_unknown_path_returns_404(self, broker: BrokerServer) -> None:
        status, body = _json_request("GET", broker, "/nope")
        assert status == 404
        assert isinstance(body, dict)
        assert body.get("error") == "not found"

    def test_full_lifecycle(
        self,
        broker: BrokerServer,
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        """End-to-end: register → discover → send → deregister → verify gone."""
        agent_srv, received = agent_server

        # 1. Register.
        status, _ = _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-full",
                "host": agent_srv.host,
                "port": agent_srv.port,
                "capabilities": {"version": "1.0"},
            },
        )
        assert status == 201

        # 2. Discover.
        status, body = _json_request("GET", broker, "/agents")
        assert status == 200
        assert isinstance(body, dict)
        assert len(body.get("agents", [])) == 1

        # 3. Send request.
        request = Request(
            metadata=Metadata.create(sender="caller", recipient="agent-full"),
            body={"action": "hello"},
        )
        status, body = _json_request("POST", broker, "/messages", serialize(request))
        assert status == 200
        reply = deserialize(json.dumps(body))
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "hello"}}

        # 4. Deregister.
        status, _ = _json_request("DELETE", broker, "/agents/agent-full")
        assert status == 204

        # 5. Discovery empty.
        status, body = _json_request("GET", broker, "/agents")
        assert status == 200
        assert body == {"agents": []}

        # 6. Send to deregistered → 404.
        request2 = Request(
            metadata=Metadata.create(sender="caller", recipient="agent-full"),
            body={"action": "are you there?"},
        )
        status, body = _json_request("POST", broker, "/messages", serialize(request2))
        assert status == 404


# ---------------------------------------------------------------------------
# TTL eviction integration tests
# ---------------------------------------------------------------------------


class TestBrokerTTLEviction:
    """Integration tests for heartbeat-based TTL eviction (child 3)."""

    def test_agent_evicted_after_ttl_expires(self) -> None:
        """Register with short TTL; after sweep runs the agent is gone."""
        broker = BrokerServer(
            host="127.0.0.1",
            port=0,
            ttl_seconds=1,
            sweep_interval_seconds=0.2,
        )
        broker.start()
        try:
            status, _ = _json_request(
                "POST",
                broker,
                "/agents",
                {"agent_id": "agent-ttl", "host": "127.0.0.1", "port": 9999},
            )
            assert status == 201

            # Agent visible immediately.
            status, body = _json_request("GET", broker, "/agents")
            assert status == 200
            assert len(body["agents"]) == 1

            # Wait for TTL + sweep.
            time.sleep(1.5)

            # Agent should be gone from discovery.
            status, body = _json_request("GET", broker, "/agents")
            assert status == 200
            assert body["agents"] == []
        finally:
            broker.stop()

    def test_evicted_agent_unreachable(self) -> None:
        """After eviction, sending to the agent returns 404."""
        broker = BrokerServer(
            host="127.0.0.1",
            port=0,
            ttl_seconds=1,
            sweep_interval_seconds=0.2,
        )
        broker.start()
        try:
            _json_request(
                "POST",
                broker,
                "/agents",
                {"agent_id": "agent-gone", "host": "127.0.0.1", "port": 9999},
            )

            time.sleep(1.5)

            request = Request(
                metadata=Metadata.create(sender="caller", recipient="agent-gone"),
                body={"action": "ping"},
            )
            status, body = _json_request(
                "POST", broker, "/messages", serialize(request)
            )
            assert status == 404
            error_msg = deserialize(json.dumps(body))
            assert isinstance(error_msg, Error)
            assert error_msg.body.get("code") == UNKNOWN_RECIPIENT
        finally:
            broker.stop()

    def test_agent_that_reregisters_stays_alive(self) -> None:
        """Re-registering before TTL expires keeps the agent alive."""
        broker = BrokerServer(
            host="127.0.0.1",
            port=0,
            ttl_seconds=2,
            sweep_interval_seconds=0.3,
        )
        broker.start()
        try:
            payload = {"agent_id": "agent-rr", "host": "127.0.0.1", "port": 9999}
            _json_request("POST", broker, "/agents", payload)

            # Re-register halfway through the TTL.
            time.sleep(1.0)
            _json_request("POST", broker, "/agents", payload)

            # Wait past the original TTL would have expired but not past
            # the refreshed one.
            time.sleep(1.5)

            # Agent should still be present.
            status, body = _json_request("GET", broker, "/agents")
            assert status == 200
            assert len(body["agents"]) == 1
            assert body["agents"][0]["agent_id"] == "agent-rr"
        finally:
            broker.stop()

    def test_eviction_disabled_with_sweep_interval_zero(self) -> None:
        """sweep_interval_seconds=0 disables the sweep thread entirely."""
        broker = BrokerServer(
            host="127.0.0.1",
            port=0,
            ttl_seconds=1,
            sweep_interval_seconds=0,
        )
        broker.start()
        try:
            _json_request(
                "POST",
                broker,
                "/agents",
                {"agent_id": "agent-nosweep", "host": "127.0.0.1", "port": 9999},
            )

            time.sleep(1.5)

            # Agent must still be present — no sweep thread was started.
            status, body = _json_request("GET", broker, "/agents")
            assert status == 200
            assert len(body["agents"]) == 1
        finally:
            broker.stop()

    def test_eviction_disabled_with_ttl_zero(self) -> None:
        """Default TTL of 0 disables expiry, even with an active sweep."""
        broker = BrokerServer(
            host="127.0.0.1",
            port=0,
            ttl_seconds=0,
            sweep_interval_seconds=0.2,
        )
        broker.start()
        try:
            _json_request(
                "POST",
                broker,
                "/agents",
                {"agent_id": "agent-nottl", "host": "127.0.0.1", "port": 9999},
            )

            time.sleep(1.0)

            status, body = _json_request("GET", broker, "/agents")
            assert status == 200
            assert len(body["agents"]) == 1
        finally:
            broker.stop()


# ---------------------------------------------------------------------------
# TLS integration tests (child 4)
# ---------------------------------------------------------------------------

try:
    import trustme  # noqa: F401

    _HAS_TRUSTME = True
except ImportError:
    _HAS_TRUSTME = False


@pytest.fixture
def broker_tls() -> Iterator[tuple[BrokerServer, ssl.SSLContext]]:
    """Yield ``(BrokerServer, client_ssl_context)`` using a self-signed cert."""
    if not _HAS_TRUSTME:
        pytest.skip("trustme not installed")

    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1")

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(server_ctx)

    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ca.configure_trust(client_ctx)

    broker = BrokerServer(host="127.0.0.1", port=0, ssl_context=server_ctx)
    broker.start()
    try:
        yield broker, client_ctx
    finally:
        broker.stop()


class TestBrokerTLSIntegration:
    """Integration tests for TLS transport encryption."""

    def test_health_over_tls(
        self, broker_tls: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = broker_tls
        status, body = _json_request("GET", broker, "/health", ssl_context=client_ctx)
        assert status == 200
        assert body == {"status": "ok"}

    def test_register_and_discover_over_tls(
        self, broker_tls: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = broker_tls
        status, body = _json_request(
            "POST",
            broker,
            "/agents",
            {"agent_id": "tls-agent", "host": "127.0.0.1", "port": 9000},
            ssl_context=client_ctx,
        )
        assert status == 201

        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert len(body["agents"]) == 1
        assert body["agents"][0]["agent_id"] == "tls-agent"

    def test_send_over_tls(
        self,
        broker_tls: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = broker_tls
        agent_srv, received = agent_server

        _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
            ssl_context=client_ctx,
        )

        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request(
            "POST", broker, "/messages", serialize(request), ssl_context=client_ctx
        )
        assert status == 200
        reply = deserialize(json.dumps(body))
        assert isinstance(reply, Response)

    def test_full_lifecycle_over_tls(
        self,
        broker_tls: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = broker_tls
        agent_srv, _ = agent_server

        # Register.
        status, _ = _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-full",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
            ssl_context=client_ctx,
        )
        assert status == 201

        # Discover.
        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert len(body["agents"]) == 1

        # Send.
        request = Request(
            metadata=Metadata.create(sender="caller", recipient="agent-full"),
            body={"action": "hello"},
        )
        status, body = _json_request(
            "POST", broker, "/messages", serialize(request), ssl_context=client_ctx
        )
        assert status == 200

        # Deregister.
        status, _ = _json_request(
            "DELETE", broker, "/agents/agent-full", ssl_context=client_ctx
        )
        assert status == 204

        # Gone from discovery.
        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert body["agents"] == []


# ---------------------------------------------------------------------------
# mTLS fixture and integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mtls_broker() -> Iterator[tuple[BrokerServer, ssl.SSLContext]]:
    """Yield ``(BrokerServer, client_ssl_context)`` with mutual TLS enabled."""
    if not _HAS_TRUSTME:
        pytest.skip("trustme not installed")

    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1")
    client_cert = ca.issue_cert("test-agent")

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(server_ctx)
    ca.configure_trust(server_ctx)

    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ca.configure_trust(client_ctx)
    client_cert.configure_cert(client_ctx)

    broker = BrokerServer(
        host="127.0.0.1", port=0, ssl_context=server_ctx, require_client_cert=True
    )
    broker.start()
    try:
        yield broker, client_ctx
    finally:
        broker.stop()


class TestBrokerMTLSIntegration:
    """Integration tests for mutual TLS (mTLS)."""

    def test_health_over_mtls(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = mtls_broker
        status, body = _json_request("GET", broker, "/health", ssl_context=client_ctx)
        assert status == 200
        assert body == {"status": "ok"}

    def test_register_and_discover_over_mtls(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = mtls_broker
        status, body = _json_request(
            "POST",
            broker,
            "/agents",
            {"agent_id": "mtls-agent", "host": "127.0.0.1", "port": 9000},
            ssl_context=client_ctx,
        )
        assert status == 201

        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert len(body["agents"]) == 1
        assert body["agents"][0]["agent_id"] == "mtls-agent"

    def test_send_over_mtls(
        self,
        mtls_broker: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = mtls_broker
        agent_srv, received = agent_server

        _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
            ssl_context=client_ctx,
        )

        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request(
            "POST", broker, "/messages", serialize(request), ssl_context=client_ctx
        )
        assert status == 200
        reply = deserialize(json.dumps(body))
        assert isinstance(reply, Response)

    def test_full_lifecycle_over_mtls(
        self,
        mtls_broker: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = mtls_broker
        agent_srv, _ = agent_server

        # Register.
        status, _ = _json_request(
            "POST",
            broker,
            "/agents",
            {
                "agent_id": "agent-full",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
            ssl_context=client_ctx,
        )
        assert status == 201

        # Discover.
        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert len(body["agents"]) == 1

        # Send.
        request = Request(
            metadata=Metadata.create(sender="caller", recipient="agent-full"),
            body={"action": "hello"},
        )
        status, body = _json_request(
            "POST", broker, "/messages", serialize(request), ssl_context=client_ctx
        )
        assert status == 200

        # Deregister.
        status, _ = _json_request(
            "DELETE", broker, "/agents/agent-full", ssl_context=client_ctx
        )
        assert status == 204

        # Gone from discovery.
        status, body = _json_request("GET", broker, "/agents", ssl_context=client_ctx)
        assert status == 200
        assert body["agents"] == []

    def test_non_mtls_client_rejected(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, _ = mtls_broker

        # Build a context that has NO CA trust and NO client certificate.
        # check_hostname=False and CERT_NONE so the TLS handshake proceeds
        # until the server requests the client certificate — which the
        # client cannot provide.  The server then aborts the connection.
        import ssl as _ssl

        bare_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        bare_ctx.check_hostname = False
        bare_ctx.verify_mode = _ssl.CERT_NONE
        # No client certificate loaded — the server will reject.

        with pytest.raises(OSError):
            _json_request("GET", broker, "/health", ssl_context=bare_ctx)


# ---------------------------------------------------------------------------
# Auth integration tests (child 4)
# ---------------------------------------------------------------------------


@pytest.fixture
def broker_with_auth() -> Iterator[BrokerServer]:
    """Yield a broker with auth enabled (two agents, each with a token)."""
    server = BrokerServer(
        host="127.0.0.1",
        port=0,
        agent_tokens={"agent-a": "tok-a", "agent-b": "tok-b"},
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


class TestBrokerAuthIntegration:
    """Integration tests for bearer-token authentication."""

    def test_health_without_token_returns_200(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request("GET", broker_with_auth, "/health")
        assert status == 200
        assert body["status"] == "ok"

    def test_agents_without_token_returns_401(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request("GET", broker_with_auth, "/agents")
        assert status == 401

    def test_register_without_token_returns_401(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request(
            "POST",
            broker_with_auth,
            "/agents",
            {"agent_id": "agent-a", "host": "127.0.0.1", "port": 9000},
        )
        assert status == 401

    def test_deregister_without_token_returns_401(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request("DELETE", broker_with_auth, "/agents/agent-a")
        assert status == 401

    def test_messages_without_token_returns_401(
        self, broker_with_auth: BrokerServer
    ) -> None:
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request(
            "POST", broker_with_auth, "/messages", serialize(request)
        )
        assert status == 401

    def test_health_with_valid_token(self, broker_with_auth: BrokerServer) -> None:
        status, body = _json_request(
            "GET",
            broker_with_auth,
            "/health",
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 200

    def test_agents_with_valid_token(self, broker_with_auth: BrokerServer) -> None:
        status, body = _json_request(
            "GET",
            broker_with_auth,
            "/agents",
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 200

    def test_register_same_agent_id_as_token(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request(
            "POST",
            broker_with_auth,
            "/agents",
            {"agent_id": "agent-a", "host": "127.0.0.1", "port": 9000},
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 201

    def test_register_different_agent_id_returns_403(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request(
            "POST",
            broker_with_auth,
            "/agents",
            {"agent_id": "agent-b", "host": "127.0.0.1", "port": 9000},
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 403
        assert "agent_id does not match token" in body["error"]

    def test_deregister_same_agent_id_as_token(
        self, broker_with_auth: BrokerServer
    ) -> None:
        # Register first.
        _json_request(
            "POST",
            broker_with_auth,
            "/agents",
            {"agent_id": "agent-a", "host": "127.0.0.1", "port": 9000},
            headers={"Authorization": "Bearer tok-a"},
        )
        # Deregister.
        status, body = _json_request(
            "DELETE",
            broker_with_auth,
            "/agents/agent-a",
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 204

    def test_deregister_different_agent_id_returns_403(
        self, broker_with_auth: BrokerServer
    ) -> None:
        status, body = _json_request(
            "DELETE",
            broker_with_auth,
            "/agents/agent-b",
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 403
        assert "agent_id does not match token" in body["error"]

    def test_send_with_valid_token(
        self,
        broker_with_auth: BrokerServer,
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        agent_srv, received = agent_server

        # Register agent-b with its own token.
        _json_request(
            "POST",
            broker_with_auth,
            "/agents",
            {
                "agent_id": "agent-b",
                "host": agent_srv.host,
                "port": agent_srv.port,
            },
            headers={"Authorization": "Bearer tok-b"},
        )

        # Send as agent-a (any valid token can send).
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        status, body = _json_request(
            "POST",
            broker_with_auth,
            "/messages",
            serialize(request),
            headers={"Authorization": "Bearer tok-a"},
        )
        assert status == 200
        reply = deserialize(json.dumps(body))
        assert isinstance(reply, Response)


# ======================================================================
# Body size limit integration tests (child 5)
# ======================================================================


class TestBrokerBodySizeLimit:
    def test_oversized_body_returns_413(self) -> None:
        broker = BrokerServer(host="127.0.0.1", port=0, max_body_size=1024)
        broker.start()
        try:
            # Send a body larger than 1024 bytes.
            big_body = "x" * 2048
            status, body = _json_request(
                "POST",
                broker,
                "/agents",
                body=big_body,
            )
            assert status == 413
            assert isinstance(body, dict)
            assert body["error"] == "request body too large"
            assert body["max_bytes"] == 1024
        finally:
            broker.stop()

    def test_body_within_limit_succeeds(self) -> None:
        broker = BrokerServer(host="127.0.0.1", port=0, max_body_size=1024)
        broker.start()
        try:
            status, body = _json_request(
                "POST",
                broker,
                "/agents",
                body={"agent_id": "a", "host": "127.0.0.1", "port": 9000},
            )
            assert status == 201
        finally:
            broker.stop()


# ======================================================================
# Rate limiting integration tests (child 5)
# ======================================================================


class TestBrokerRateLimiting:
    def test_burst_exceeds_limit_returns_429(self) -> None:
        broker = BrokerServer(host="127.0.0.1", port=0, rate_limit_per_second=5.0)
        broker.start()
        try:
            statuses: list[int] = []
            for _ in range(10):
                status, _ = _json_request(
                    "POST",
                    broker,
                    "/agents",
                    body={"agent_id": "a", "host": "127.0.0.1", "port": 9000},
                )
                statuses.append(status)

            # At least some requests should be rate-limited.
            assert 429 in statuses, f"Expected some 429 responses, got {statuses}"
        finally:
            broker.stop()

    def test_429_includes_retry_after_header(self) -> None:
        broker = BrokerServer(host="127.0.0.1", port=0, rate_limit_per_second=3.0)
        broker.start()
        try:
            # Send many rapid requests.
            for _ in range(20):
                status, _ = _json_request(
                    "POST",
                    broker,
                    "/agents",
                    body={"agent_id": "a", "host": "127.0.0.1", "port": 9000},
                )
                if status == 429:
                    # Make a raw connection to check headers.
                    import http.client

                    conn = http.client.HTTPConnection(
                        broker.host, broker.port, timeout=5.0
                    )
                    try:
                        payload = json.dumps(
                            {"agent_id": "a", "host": "127.0.0.1", "port": 9000}
                        ).encode("utf-8")
                        conn.request(
                            "POST",
                            "/agents",
                            body=payload,
                            headers={
                                "Content-Type": "application/json",
                            },
                        )
                        resp = conn.getresponse()
                        assert resp.getheader("Retry-After") == "1"
                        resp.read()
                    finally:
                        conn.close()
                    break
        finally:
            broker.stop()

    def test_rate_limit_zero_disables(self) -> None:
        broker = BrokerServer(host="127.0.0.1", port=0)  # rate_limit_per_second=0.0
        broker.start()
        try:
            for _ in range(20):
                status, _ = _json_request(
                    "POST",
                    broker,
                    "/agents",
                    body={"agent_id": "a", "host": "127.0.0.1", "port": 9000},
                )
                assert status != 429, "Rate limiting should be disabled"
        finally:
            broker.stop()


# ======================================================================
# Audit logging integration tests (child 5)
# ======================================================================


class TestBrokerAuditLogging:
    def test_audit_log_writes_json_lines(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="r+", delete=False, suffix=".jsonl"
        ) as tf:
            log_path = tf.name

        try:
            broker = BrokerServer(host="127.0.0.1", port=0, audit_log_path=log_path)
            broker.start()
            try:
                # Register.
                _json_request(
                    "POST",
                    broker,
                    "/agents",
                    body={"agent_id": "log-agent", "host": "127.0.0.1", "port": 9000},
                )
                # Send a message to it (register a second agent as recipient first).
                _json_request(
                    "POST",
                    broker,
                    "/agents",
                    body={"agent_id": "log-agent-b", "host": "127.0.0.1", "port": 9001},
                )
                _json_request(
                    "POST",
                    broker,
                    "/messages",
                    body=serialize(
                        Request(
                            metadata=Metadata.create(
                                sender="log-agent", recipient="log-agent-b"
                            ),
                            body={"action": "ping"},
                        )
                    ),
                )
                # Deregister.
                _json_request("DELETE", broker, "/agents/log-agent")
            finally:
                broker.stop()

            # Read the log file.
            with open(log_path, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

            assert len(lines) >= 3, f"Expected at least 3 audit lines, got {len(lines)}"

            actions = []
            for line in lines:
                record = json.loads(line)
                assert "timestamp" in record
                assert "action" in record
                assert "agent_id" in record
                assert "path" in record
                assert "status" in record
                assert "detail" in record
                actions.append(record["action"])

            assert "register" in actions
            assert "send" in actions
            assert "deregister" in actions
        finally:
            import os

            if os.path.exists(log_path):
                os.unlink(log_path)

    def test_audit_disabled_when_path_none(self) -> None:
        """When audit_log_path is None, the server starts and stops
        without errors (audit logger writes to stdout silently).
        """
        broker = BrokerServer(host="127.0.0.1", port=0)  # audit_log_path=None
        broker.start()
        try:
            status, _ = _json_request(
                "POST",
                broker,
                "/agents",
                body={"agent_id": "x", "host": "127.0.0.1", "port": 9000},
            )
            assert status == 201
        finally:
            broker.stop()
