"""Integration tests for the broker server with real servers.

Follows the pattern from ``tests/transport/test_server_client.py``:
ephemeral-port servers, no mocks, real HTTP calls.
"""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable, Iterator

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
from robotsix_agent_comm.transport import TransportServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _echo_handler(
    received: list[Message],
) -> Callable[[Message], Message | None]:
    def handler(message: Message) -> Message | None:
        received.append(message)
        if isinstance(message, Request):
            return Response.to(message, body={"echo": message.body})
        return None

    return handler


def _json_request(
    method: str,
    broker: BrokerServer,
    path: str,
    body: dict[str, object] | list[object] | str | None = None,
) -> tuple[int, object]:
    """Make a raw HTTP request to the broker, return (status, parsed_body)."""
    conn = http.client.HTTPConnection(broker.host, broker.port, timeout=5.0)
    try:
        if isinstance(body, str):
            payload = body.encode("utf-8")
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        else:
            payload = None

        conn.request(
            method,
            path,
            body=payload,
            headers={"Content-Type": "application/json"} if payload else {},
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def broker() -> Iterator[BrokerServer]:
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def agent_server() -> Iterator[tuple[TransportServer, list[Message]]]:
    received: list[Message] = []
    server = TransportServer(_echo_handler(received), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server, received
    finally:
        server.stop()


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
        assert error_msg.body.get("code") == "unknown_recipient"

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
