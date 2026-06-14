"""End-to-end transport tests over a real ephemeral-port server."""

from __future__ import annotations

import http.client
from collections.abc import Callable, Iterator

import pytest

from robotsix_agent_comm.protocol import (
    Message,
    Metadata,
    Notification,
    Request,
    Response,
    serialize,
)
from robotsix_agent_comm.transport import (
    AgentNotFoundError,
    DeliveryError,
    Endpoint,
    Registry,
    RetryPolicy,
    Router,
    TransportClient,
    TransportError,
    TransportServer,
)


def _echo_handler(
    received: list[Message],
) -> Callable[[Message], Message | None]:
    def handler(message: Message) -> Message | None:
        received.append(message)
        if isinstance(message, Request):
            return Response.to(message, body={"echo": message.body})
        return None

    return handler


@pytest.fixture
def running_server() -> Iterator[tuple[TransportServer, list[Message]]]:
    received: list[Message] = []
    server = TransportServer(_echo_handler(received), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server, received
    finally:
        server.stop()


def _endpoint_for(server: TransportServer) -> Endpoint:
    return Endpoint(agent_id="agent-b", host=server.host, port=server.port)


def test_route_request_round_trips(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    server, received = running_server
    registry = Registry()
    registry.register(_endpoint_for(server))
    router = Router(
        registry,
        TransportClient(),
        RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
    )

    request = Request(
        metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
        body={"action": "ping"},
    )
    response = router.route(request)

    assert len(received) == 1
    assert received[0].body == {"action": "ping"}
    assert isinstance(response, Response)
    assert response.body == {"echo": {"action": "ping"}}
    assert response.correlation_id == request.message_id


def test_route_unknown_recipient_raises(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    registry = Registry()
    router = Router(
        registry,
        TransportClient(),
        RetryPolicy(max_attempts=1, base_delay=0.0, max_delay=0.0),
    )
    request = Request(
        metadata=Metadata.create(sender="agent-a", recipient="ghost"),
        body={},
    )
    with pytest.raises(AgentNotFoundError):
        router.route(request)


def test_delivery_error_when_unreachable() -> None:
    registry = Registry()
    # Register an endpoint pointing at a port nothing listens on.
    registry.register(Endpoint(agent_id="agent-b", host="127.0.0.1", port=1))
    router = Router(
        registry,
        TransportClient(),
        RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        timeout=0.2,
    )
    request = Request(
        metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
        body={},
    )
    with pytest.raises(DeliveryError):
        router.route(request)


def test_health_check_live_and_dead(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    server, _ = running_server
    client = TransportClient()
    endpoint = _endpoint_for(server)
    assert client.health_check(endpoint, timeout=1.0) is True

    dead = Endpoint(agent_id="agent-c", host="127.0.0.1", port=1)
    assert client.health_check(dead, timeout=0.2) is False


def test_notification_returns_none(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    server, received = running_server
    client = TransportClient()
    note = Notification(
        metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
        body={"event": "tick"},
    )
    result = client.send(note, _endpoint_for(server), timeout=1.0)
    assert result is None
    assert len(received) == 1


def test_malformed_body_returns_4xx(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    server, _ = running_server
    conn = http.client.HTTPConnection(server.host, server.port, timeout=1.0)
    try:
        conn.request("POST", "/messages", body=b"{not json")
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    assert 400 <= response.status < 500
    assert b"error" in body


def test_client_send_to_unknown_path_raises(
    running_server: tuple[TransportServer, list[Message]],
) -> None:
    server, _ = running_server
    client = TransportClient()
    endpoint = Endpoint(
        agent_id="agent-b", host=server.host, port=server.port, path="/wrong"
    )
    request = Request(
        metadata=Metadata.create(sender="agent-a", recipient="agent-b"),
        body={},
    )
    with pytest.raises(TransportError):
        client.send(request, endpoint, timeout=1.0)
    # Sanity: serialization of the request is well-formed.
    assert serialize(request)
