"""Integration tests for NAT-safe pull/mailbox delivery via the broker.

Two pull-mode agents (no local listener — they register a mailbox and receive
by long-polling ``GET /messages``) exchange a request/response and a
notification entirely through a running :class:`BrokerServer`. Also covers the
long-poll timeout and registering a mailbox endpoint.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Message, Request, Response
from robotsix_agent_comm.sdk.agent import Agent
from robotsix_agent_comm.transport import Endpoint
from robotsix_agent_comm.transport.brokered import (
    BrokeredRegistry,
    NetworkedBrokerTransport,
    create_transport_pair,
)


@pytest.fixture
def broker() -> Generator[BrokerServer, None, None]:
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _pull_agent(agent_id: str, broker: BrokerServer) -> Agent:
    registry, transport = create_transport_pair(
        "brokered", broker_host=broker.host, broker_port=broker.port
    )
    return Agent(agent_id, registry, transport=transport, pull=True, timeout=5.0)


def test_request_response_via_mailbox(broker: BrokerServer) -> None:
    received: list[Message] = []
    responder = _pull_agent("responder", broker)
    requester = _pull_agent("requester", broker)

    @responder.on_request
    def _handle(request: Request) -> Message:
        received.append(request)
        return Response.to(request, body={"echo": request.body})

    with responder, requester:
        reply = requester.send_request("responder", {"action": "ping"}, timeout=5.0)

    assert isinstance(reply, Response)
    assert reply.body == {"echo": {"action": "ping"}}
    assert len(received) == 1
    assert received[0].body == {"action": "ping"}


def test_notification_via_mailbox(broker: BrokerServer) -> None:
    got: list[Message] = []
    listener = _pull_agent("listener", broker)
    sender = _pull_agent("sender", broker)

    @listener.on_notification
    def _handle(notification: Message) -> None:
        got.append(notification)

    with listener, sender:
        sender.send_notification("listener", {"event": "spike"})
        # Give the listener's receive-loop a moment to pull + dispatch.
        msg = listener.receive_message(timeout=5.0)

    assert msg.body == {"event": "spike"}
    assert got and got[0].body == {"event": "spike"}


def test_poll_timeout_returns_empty(broker: BrokerServer) -> None:
    registry = BrokeredRegistry(broker.host, broker.port)
    transport = NetworkedBrokerTransport(broker.host, broker.port)
    registry.register(Endpoint(agent_id="idle", host="mailbox", port=0, mailbox=True))

    messages = transport.receive("idle", wait=0.3, timeout=5.0)
    assert messages == []
