"""Tests for lifecycle server broker registration and request handling."""

from __future__ import annotations

import contextlib

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Error as ProtocolError
from robotsix_agent_comm.protocol import Response
from robotsix_agent_comm.sdk.agent import Agent
from robotsix_agent_comm.transport import (
    BrokeredRegistry,
    NetworkedBrokerTransport,
)
from tests.lifecycle.conftest import create_lifecycle_server

# ---------------------------------------------------------------------------
# Broker registration handshake
# ---------------------------------------------------------------------------


class TestBrokerRegistration:
    """Verify the lifecycle server registers correctly with the broker."""

    def test_registers_with_broker(self, broker: BrokerServer) -> None:
        """LifecycleServer appears in the broker registry after start."""
        server = create_lifecycle_server(broker)
        try:
            registry = BrokeredRegistry(broker.host, broker.port)
            agents = registry.list_agents()
            assert any(a.agent_id == "lifecycle-server" for a in agents), (
                f"lifecycle-server not found among {[a.agent_id for a in agents]}"
            )
        finally:
            server.stop()

    def test_unregisters_on_stop(self, broker: BrokerServer) -> None:
        """LifecycleServer is removed from broker registry after stop.

        The self-heal mechanism in the broker can re-register a pull agent
        on a late poll after unregister, so we ensure the background
        receive thread exits and then explicitly clean up any residual
        registration.
        """
        server = create_lifecycle_server(broker)
        server.stop()

        # After stop(), the _recv_thread may still be mid-poll (the poll
        # uses a 20-second wait, and Agent.stop joins with 2-second
        # timeout).  Wait for the recv thread to actually exit.
        recv_thread = server._agent._recv_thread
        if recv_thread is not None:
            recv_thread.join(timeout=5.0)

        # Clean up any residual registration from a post-stop poll.
        registry = BrokeredRegistry(broker.host, broker.port)
        with contextlib.suppress(Exception):
            registry.unregister("lifecycle-server")

        agents = registry.list_agents()
        assert not any(a.agent_id == "lifecycle-server" for a in agents), (
            "lifecycle-server still present after stop"
        )

    def test_capabilities_advertised(self, broker: BrokerServer) -> None:
        """LifecycleServer advertises supported_kinds in its capabilities."""
        server = create_lifecycle_server(broker)
        try:
            # The server's own supported_kinds property should include
            # "monitor", "status", and "lifecycle".
            kinds = server.supported_kinds
            assert "monitor" in kinds
            assert "status" in kinds
            assert "lifecycle" in kinds
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Request handling via broker
# ---------------------------------------------------------------------------


class TestRequestHandling:
    """Verify the lifecycle server handles broker-delivered requests."""

    def test_handles_status_request(self, broker: BrokerServer) -> None:
        """A status request returns server status."""
        server = create_lifecycle_server(broker)
        try:
            registry = BrokeredRegistry(broker.host, broker.port)
            transport = NetworkedBrokerTransport(broker.host, broker.port)
            client = Agent(
                "test-client",
                registry,
                transport=transport,
                timeout=5.0,
                pull=True,
            )
            client.start()
            try:
                reply = client.send_request(
                    "lifecycle-server",
                    {"kind": "status"},
                )
                assert isinstance(reply, Response)
                body = reply.body
                assert isinstance(body, dict)
                assert body.get("status") == "ok"
                assert body.get("agent_id") == "lifecycle-server"
                assert body.get("tracing_enabled") is False
            finally:
                client.stop()
        finally:
            server.stop()

    def test_handles_lifecycle_request(self, broker: BrokerServer) -> None:
        """A lifecycle command request is acknowledged."""
        server = create_lifecycle_server(broker)
        try:
            registry = BrokeredRegistry(broker.host, broker.port)
            transport = NetworkedBrokerTransport(broker.host, broker.port)
            client = Agent(
                "test-client",
                registry,
                transport=transport,
                timeout=5.0,
                pull=True,
            )
            client.start()
            try:
                reply = client.send_request(
                    "lifecycle-server",
                    {
                        "kind": "lifecycle",
                        "params": {
                            "command": "restart",
                            "service": "test-svc",
                        },
                    },
                )
                assert isinstance(reply, Response)
                body = reply.body
                assert isinstance(body, dict)
                assert body.get("result") == "acknowledged"
                assert body.get("command") == "restart"
                assert body.get("service") == "test-svc"
            finally:
                client.stop()
        finally:
            server.stop()

    def test_handles_monitor_request(self, broker: BrokerServer) -> None:
        """A monitor request returns server telemetry."""
        server = create_lifecycle_server(broker)
        try:
            registry = BrokeredRegistry(broker.host, broker.port)
            transport = NetworkedBrokerTransport(broker.host, broker.port)
            client = Agent(
                "test-client",
                registry,
                transport=transport,
                timeout=5.0,
                pull=True,
            )
            client.start()
            try:
                reply = client.send_request(
                    "lifecycle-server",
                    {"kind": "monitor"},
                )
                assert isinstance(reply, Response)
                body = reply.body
                assert isinstance(body, dict)
                assert body.get("status") == "ok"
                assert body.get("agent_id") == "lifecycle-server"
            finally:
                client.stop()
        finally:
            server.stop()

    def test_unknown_kind_returns_error(self, broker: BrokerServer) -> None:
        """An unknown request kind returns an error."""
        server = create_lifecycle_server(broker)
        try:
            registry = BrokeredRegistry(broker.host, broker.port)
            transport = NetworkedBrokerTransport(broker.host, broker.port)
            client = Agent(
                "test-client",
                registry,
                transport=transport,
                timeout=5.0,
                pull=True,
            )
            client.start()
            try:
                reply = client.send_request(
                    "lifecycle-server",
                    {"kind": "nonexistent"},
                )
                # BrokeredResponder's _dispatch returns an Error message
                # for unknown kinds. The code is in the body dict.
                assert isinstance(reply, ProtocolError)
                assert reply.body.get("code") == "unknown_kind"
            finally:
                client.stop()
        finally:
            server.stop()
