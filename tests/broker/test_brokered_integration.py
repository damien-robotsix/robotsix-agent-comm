"""Integration tests for BrokeredRegistry and NetworkedBrokerTransport.

Uses real running BrokerServer and TransportServer instances — no HTTP mocks.
Follows the same fixture pattern as ``test_broker_integration.py``.
"""

from __future__ import annotations

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
            registry,  # type: ignore[arg-type]
            transport=transport,
            timeout=3.0,
        )
        bob = Agent(
            "bob",
            registry,  # type: ignore[arg-type]
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
            registry,  # type: ignore[arg-type]
            transport=transport,
            timeout=3.0,
        )
        bob = Agent(
            "bob",
            registry,  # type: ignore[arg-type]
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
            registry,  # type: ignore[arg-type]
            transport=transport,
            timeout=3.0,
        )
        alice.start()
        try:
            with pytest.raises(AgentNotFoundError):
                alice.send_request("ghost", {"action": "hello"})
        finally:
            alice.stop()
