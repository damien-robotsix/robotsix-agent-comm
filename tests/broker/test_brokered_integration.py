"""Integration tests for BrokeredRegistry and NetworkedBrokerTransport.

Uses real running BrokerServer and TransportServer instances — no HTTP mocks.
Follows the same fixture pattern as ``test_broker_integration.py``.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Callable, Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import (
    Message,
    Metadata,
    Notification,
    Request,
    Response,
)
from robotsix_agent_comm.sdk.agent import Agent
from robotsix_agent_comm.transport import (
    AgentNotFoundError,
    Endpoint,
    TransportServer,
)
from robotsix_agent_comm.transport.brokered import (
    BrokeredRegistry,
    NetworkedBrokerTransport,
    create_transport_pair,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _echo_handler(received: list[Message]) -> Callable[[Message], Message | None]:
    def handler(message: Message) -> Message | None:
        received.append(message)
        if isinstance(message, Request):
            return Response.to(message, body={"echo": message.body})
        return None

    return handler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def broker() -> Generator[BrokerServer, None, None]:
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def agent_server() -> Generator[tuple[TransportServer, list[Message]], None, None]:
    received: list[Message] = []
    server = TransportServer(_echo_handler(received), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server, received
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Tests – BrokeredRegistry + NetworkedBrokerTransport (low-level)
# ---------------------------------------------------------------------------


class TestBrokeredRegistryAndTransport:
    """Direct usage of BrokeredRegistry and NetworkedBrokerTransport."""

    def test_full_brokered_lifecycle(
        self,
        broker: BrokerServer,
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        """Register, lookup, list, send, and verify delivery via broker."""
        agent_srv, received = agent_server

        # 1. Create a BrokeredRegistry pointing at the broker.
        registry = BrokeredRegistry(broker.host, broker.port)

        # 2. Create an Endpoint for the agent server.
        endpoint = Endpoint(
            agent_id="agent-b",
            host=agent_srv.host,
            port=agent_srv.port,
        )

        # 3. Register the endpoint via BrokeredRegistry.
        registry.register(endpoint)

        # 4. Lookup: should find the agent.
        looked_up = registry.lookup("agent-b")
        assert looked_up.agent_id == "agent-b"

        # 5. List agents: should contain the agent.
        agents = registry.list_agents()
        assert any(a.agent_id == "agent-b" for a in agents)

        # 6. Create a NetworkedBrokerTransport pointing at the broker.
        transport = NetworkedBrokerTransport(broker.host, broker.port)

        # 7. Send a Request via the transport to a placeholder endpoint.
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        reply = transport.send(request, endpoint, timeout=5.0)

        # 8. Verify the agent server received the request.
        assert len(received) == 1
        assert received[0].body == {"action": "ping"}

        # 9. Verify the reply is a Response with echo body.
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}
        assert reply.correlation_id == request.message_id

    def test_agent_not_found_on_unknown_recipient(self, broker: BrokerServer) -> None:
        """BrokeredRegistry.lookup("ghost") raises AgentNotFoundError."""
        registry = BrokeredRegistry(broker.host, broker.port)

        with pytest.raises(AgentNotFoundError):
            registry.lookup("ghost")

    def test_error_after_deregistration(
        self,
        broker: BrokerServer,
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        """After deregistration, sending raises AgentNotFoundError."""
        agent_srv, _received = agent_server

        registry = BrokeredRegistry(broker.host, broker.port)
        endpoint = Endpoint(
            agent_id="agent-b",
            host=agent_srv.host,
            port=agent_srv.port,
        )

        # Register, then deregister.
        registry.register(endpoint)
        registry.unregister("agent-b")

        # Sending via NetworkedBrokerTransport should raise AgentNotFoundError
        # because the broker returns 404 with unknown_recipient envelope.
        transport = NetworkedBrokerTransport(broker.host, broker.port)
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "are you there?"},
        )

        with pytest.raises(AgentNotFoundError):
            transport.send(request, endpoint, timeout=5.0)


# ---------------------------------------------------------------------------
# Tests – Agent SDK with brokered components
# ---------------------------------------------------------------------------


class TestAgentWithBrokeredComponents:
    """Agent SDK integration using BrokeredRegistry + NetworkedBrokerTransport."""

    def test_two_agents_request_reply(
        self,
        broker: BrokerServer,
    ) -> None:
        """Two Agents communicate via broker using send_request()."""
        registry = BrokeredRegistry(broker.host, broker.port)
        transport = NetworkedBrokerTransport(broker.host, broker.port)

        bob_received: list[Message] = []

        def bob_handler(message: Message) -> Message | None:
            bob_received.append(message)
            if isinstance(message, Request):
                return Response.to(message, body={"from_bob": message.body})
            return None

        alice = Agent(
            "alice",
            registry,
            transport=transport,
            timeout=3.0,
        )
        bob = Agent(
            "bob",
            registry,
            transport=transport,
            timeout=3.0,
        )

        bob.on_request(bob_handler)

        alice.start()
        bob.start()

        try:
            reply = alice.send_request("bob", {"action": "hello"})
            assert isinstance(reply, Response)
            assert reply.body == {"from_bob": {"action": "hello"}}
            assert len(bob_received) == 1
            assert bob_received[0].body == {"action": "hello"}
        finally:
            bob.stop()
            alice.stop()

    def test_two_agents_notification(
        self,
        broker: BrokerServer,
    ) -> None:
        """Notification sent via broker arrives at the recipient Agent."""
        registry = BrokeredRegistry(broker.host, broker.port)
        transport = NetworkedBrokerTransport(broker.host, broker.port)

        alice_received: list[Message] = []

        def alice_handler(notification: Notification) -> None:
            alice_received.append(notification)

        alice = Agent(
            "alice",
            registry,
            transport=transport,
            timeout=3.0,
        )
        bob = Agent(
            "bob",
            registry,
            transport=transport,
            timeout=3.0,
        )

        alice.on_notification(alice_handler)

        alice.start()
        bob.start()

        try:
            bob.send_notification("alice", {"event": "tick"})
            # Give the notification a moment to propagate.
            time.sleep(0.3)

            assert len(alice_received) == 1
            assert isinstance(alice_received[0], Notification)
            assert alice_received[0].body == {"event": "tick"}
        finally:
            bob.stop()
            alice.stop()

    def test_agent_not_found_during_send(
        self,
        broker: BrokerServer,
    ) -> None:
        """send_request() to an unregistered recipient raises AgentNotFoundError."""
        registry = BrokeredRegistry(broker.host, broker.port)
        transport = NetworkedBrokerTransport(broker.host, broker.port)

        alice = Agent(
            "alice",
            registry,
            transport=transport,
            timeout=3.0,
        )
        alice.start()
        try:
            with pytest.raises(AgentNotFoundError):
                alice.send_request("ghost", {"action": "hello"})
        finally:
            alice.stop()


# ---------------------------------------------------------------------------
# Auth integration tests (child 4)
# ---------------------------------------------------------------------------


@pytest.fixture
def broker_with_auth() -> Generator[BrokerServer, None, None]:
    """Yield a broker with per-agent bearer-token auth enabled."""
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


class TestBrokeredAuthIntegration:
    """BrokeredRegistry and NetworkedBrokerTransport with auth-enabled broker."""

    def test_register_with_token(self, broker_with_auth: BrokerServer) -> None:
        registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        endpoint = Endpoint(agent_id="agent-a", host="127.0.0.1", port=9000)
        registry.register(endpoint)

        # Verify via discovery (same token).
        agents = registry.list_agents()
        assert any(a.agent_id == "agent-a" for a in agents)

    def test_register_with_wrong_token_does_not_register(
        self, broker_with_auth: BrokerServer
    ) -> None:
        """Register with bad token is fire-and-forget but the agent
        is not actually registered — verified via a valid-token lookup."""
        # Attempt registration with a bad token.
        bad_registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="bad-token"
        )
        endpoint = Endpoint(agent_id="agent-a", host="127.0.0.1", port=9000)
        # register() does not raise — it's fire-and-forget.
        bad_registry.register(endpoint)

        # Look up with a valid token — agent was never registered.
        good_registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        with pytest.raises(AgentNotFoundError):
            good_registry.lookup("agent-a")

    def test_lookup_and_list_with_valid_token(
        self, broker_with_auth: BrokerServer
    ) -> None:
        registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        endpoint = Endpoint(agent_id="agent-a", host="127.0.0.1", port=9000)
        registry.register(endpoint)

        looked_up = registry.lookup("agent-a")
        assert looked_up.agent_id == "agent-a"

        agents = registry.list_agents()
        assert any(a.agent_id == "agent-a" for a in agents)

    def test_send_with_valid_token(
        self,
        broker_with_auth: BrokerServer,
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        agent_srv, received = agent_server

        # Register agent-b with its token.
        reg_b = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-b"
        )
        ep_b = Endpoint(agent_id="agent-b", host=agent_srv.host, port=agent_srv.port)
        reg_b.register(ep_b)

        # Send from agent-a's transport.
        transport = NetworkedBrokerTransport(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        reply = transport.send(request, ep_b, timeout=5.0)

        assert len(received) == 1
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}

    def test_health_check_with_valid_token(
        self, broker_with_auth: BrokerServer
    ) -> None:
        transport = NetworkedBrokerTransport(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        result = transport.health_check(
            Endpoint(agent_id="agent-a", host="127.0.0.1", port=9000), timeout=5.0
        )
        assert result is True

    def test_health_check_without_token_returns_false(
        self, broker_with_auth: BrokerServer
    ) -> None:
        transport = NetworkedBrokerTransport(
            broker_with_auth.host, broker_with_auth.port
        )
        result = transport.health_check(
            Endpoint(agent_id="agent-a", host="127.0.0.1", port=9000), timeout=5.0
        )
        assert result is False

    def test_two_agents_with_auth_request_reply(
        self, broker_with_auth: BrokerServer
    ) -> None:
        """Two Agents communicate through an auth-enabled broker."""
        alice_registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        alice_transport = NetworkedBrokerTransport(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-a"
        )
        bob_registry = BrokeredRegistry(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-b"
        )
        bob_transport = NetworkedBrokerTransport(
            broker_with_auth.host, broker_with_auth.port, agent_token="tok-b"
        )

        bob_received: list[Message] = []

        def bob_handler(message: Message) -> Message | None:
            bob_received.append(message)
            if isinstance(message, Request):
                return Response.to(message, body={"from_bob": message.body})
            return None

        # Agent IDs must match the tokens: tok-a → agent-a, tok-b → agent-b.
        alice = Agent(
            "agent-a",
            alice_registry,
            transport=alice_transport,
            timeout=3.0,
        )
        bob = Agent(
            "agent-b",
            bob_registry,
            transport=bob_transport,
            timeout=3.0,
        )

        bob.on_request(bob_handler)

        alice.start()
        bob.start()

        try:
            reply = alice.send_request("agent-b", {"action": "hello"})
            assert isinstance(reply, Response)
            assert reply.body == {"from_bob": {"action": "hello"}}
            assert len(bob_received) == 1
        finally:
            bob.stop()
            alice.stop()


# ---------------------------------------------------------------------------
# TLS + brokered integration tests (child 4)
# ---------------------------------------------------------------------------

try:
    import trustme  # noqa: F401

    _HAS_TRUSTME = True
except ImportError:
    _HAS_TRUSTME = False


@pytest.fixture
def broker_tls() -> Generator[tuple[BrokerServer, ssl.SSLContext], None, None]:
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


class TestBrokeredTLSIntegration:
    """BrokeredRegistry and NetworkedBrokerTransport over TLS."""

    def test_register_and_lookup_over_tls(
        self, broker_tls: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = broker_tls
        registry = BrokeredRegistry(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        endpoint = Endpoint(agent_id="tls-agent", host="127.0.0.1", port=9000)
        registry.register(endpoint)

        looked_up = registry.lookup("tls-agent")
        assert looked_up.agent_id == "tls-agent"

    def test_send_over_tls(
        self,
        broker_tls: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = broker_tls
        agent_srv, received = agent_server

        registry = BrokeredRegistry(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        endpoint = Endpoint(
            agent_id="agent-b", host=agent_srv.host, port=agent_srv.port
        )
        registry.register(endpoint)

        transport = NetworkedBrokerTransport(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        reply = transport.send(request, endpoint, timeout=5.0)

        assert len(received) == 1
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}

    def test_health_check_over_tls(
        self, broker_tls: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = broker_tls
        transport = NetworkedBrokerTransport(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        result = transport.health_check(
            Endpoint(agent_id="a", host="127.0.0.1", port=1), timeout=5.0
        )
        assert result is True

    def test_create_transport_pair_with_tls(
        self, broker_tls: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = broker_tls
        reg, transport = create_transport_pair(
            "brokered",
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="https",
            broker_ssl_context=client_ctx,
        )
        assert isinstance(reg, BrokeredRegistry)
        assert isinstance(transport, NetworkedBrokerTransport)
        assert reg._ssl_context is client_ctx
        assert transport._ssl_context is client_ctx


# ---------------------------------------------------------------------------
# mTLS fixture and brokered integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mtls_broker() -> Generator[tuple[BrokerServer, ssl.SSLContext], None, None]:
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


class TestBrokeredMTLSIntegration:
    """BrokeredRegistry and NetworkedBrokerTransport over mutual TLS."""

    def test_register_and_lookup_over_mtls(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = mtls_broker
        registry = BrokeredRegistry(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        endpoint = Endpoint(agent_id="mtls-agent", host="127.0.0.1", port=9000)
        registry.register(endpoint)

        looked_up = registry.lookup("mtls-agent")
        assert looked_up.agent_id == "mtls-agent"

    def test_send_over_mtls(
        self,
        mtls_broker: tuple[BrokerServer, ssl.SSLContext],
        agent_server: tuple[TransportServer, list[Message]],
    ) -> None:
        broker, client_ctx = mtls_broker
        agent_srv, received = agent_server

        registry = BrokeredRegistry(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        endpoint = Endpoint(
            agent_id="agent-b", host=agent_srv.host, port=agent_srv.port
        )
        registry.register(endpoint)

        transport = NetworkedBrokerTransport(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        request = Request(
            metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
            body={"action": "ping"},
        )
        reply = transport.send(request, endpoint, timeout=5.0)

        assert len(received) == 1
        assert isinstance(reply, Response)
        assert reply.body == {"echo": {"action": "ping"}}

    def test_health_check_over_mtls(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = mtls_broker
        transport = NetworkedBrokerTransport(
            broker.host, broker.port, scheme="https", ssl_context=client_ctx
        )
        result = transport.health_check(
            Endpoint(agent_id="a", host="127.0.0.1", port=1), timeout=5.0
        )
        assert result is True

    def test_create_transport_pair_with_mtls(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, client_ctx = mtls_broker
        reg, transport = create_transport_pair(
            "brokered",
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="https",
            broker_ssl_context=client_ctx,
        )
        assert isinstance(reg, BrokeredRegistry)
        assert isinstance(transport, NetworkedBrokerTransport)

    def test_non_mtls_transport_rejected(
        self, mtls_broker: tuple[BrokerServer, ssl.SSLContext]
    ) -> None:
        broker, _ = mtls_broker

        # Build a context that trusts the server but has no client cert.
        import ssl as _ssl

        bare_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        bare_ctx.check_hostname = False
        bare_ctx.verify_mode = _ssl.CERT_NONE

        transport = NetworkedBrokerTransport(
            broker.host, broker.port, scheme="https", ssl_context=bare_ctx
        )

        # health_check should return False instead of raising.
        result = transport.health_check(
            Endpoint(agent_id="a", host="127.0.0.1", port=1), timeout=5.0
        )
        assert result is False
