"""End-to-end SDK tests over real in-process agents and transport."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.protocol import (
    Error,
    MessageType,
    Notification,
    Request,
    Response,
)
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import AgentNotFoundError, Registry


@pytest.fixture
def registry() -> Registry:
    return Registry()


def test_start_registers_and_stop_unregisters(registry: Registry) -> None:
    agent = Agent("agent-a", registry)
    agent.start()
    try:
        endpoint = registry.lookup("agent-a")
        assert endpoint.agent_id == "agent-a"
        assert endpoint.port != 0
    finally:
        agent.stop()
    with pytest.raises(AgentNotFoundError):
        registry.lookup("agent-a")


def test_context_manager_round_trips_request(registry: Registry) -> None:
    responder = Agent("responder", registry)

    @responder.on_request
    def handle(request: Request) -> Response:
        return Response.to(request, body={"echo": request.body})

    requester = Agent("requester", registry)

    with responder, requester:
        request_ids: list[str] = []

        @responder.on_request
        def capture(request: Request) -> Response:
            request_ids.append(request.message_id)
            return Response.to(request, body={"echo": request.body})

        reply = requester.send_request("responder", {"action": "ping"}, timeout=2.0)

    assert isinstance(reply, Response)
    assert reply.body == {"echo": {"action": "ping"}}
    assert reply.correlation_id == request_ids[0]


def test_send_notification_reaches_handler(registry: Registry) -> None:
    received: list[Notification] = []

    listener = Agent("listener", registry)

    @listener.on_notification
    def handle(notification: Notification) -> None:
        received.append(notification)

    publisher = Agent("publisher", registry)

    with listener, publisher:
        publisher.send_notification("listener", {"event": "tick"})
        message = listener.receive_message(timeout=2.0)

    assert message.body == {"event": "tick"}
    assert len(received) == 1
    assert received[0].body == {"event": "tick"}


def test_receive_message_yields_inbound_request(registry: Registry) -> None:
    responder = Agent("responder", registry)

    @responder.on_request
    def handle(request: Request) -> Response:
        return Response.to(request, body={"ok": True})

    requester = Agent("requester", registry)

    with responder, requester:
        requester.send_request("responder", {"action": "ping"}, timeout=2.0)
        inbound = responder.receive_message(timeout=2.0)

    assert inbound.type is MessageType.REQUEST
    assert inbound.body == {"action": "ping"}


def test_send_to_unknown_recipient_raises(registry: Registry) -> None:
    sender = Agent("sender", registry)
    with sender, pytest.raises(AgentNotFoundError):
        sender.send_request("ghost", {}, timeout=2.0)


def test_handler_error_reply_is_delivered(registry: Registry) -> None:
    service = Agent("service", registry)

    @service.on_request
    def handle(request: Request) -> Error:
        return Error.to(request, code="boom", message="nope")

    client = Agent("client", registry)

    with service, client:
        reply = client.send_request("service", {}, timeout=2.0)

    assert reply.type is MessageType.ERROR
    assert reply.body["code"] == "boom"
    assert reply.body["message"] == "nope"


def test_unhandled_request_returns_error_reply(registry: Registry) -> None:
    silent = Agent("silent", registry)
    client = Agent("client", registry)

    with silent, client:
        reply = client.send_request("silent", {}, timeout=2.0)

    assert reply.type is MessageType.ERROR
    assert reply.body["code"] == "no_handler"
