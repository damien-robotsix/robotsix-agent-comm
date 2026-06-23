"""Integration tests for :class:`~.brokered_request.BrokeredRequester`."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Error, Message, Request, Response
from robotsix_agent_comm.sdk import BrokeredAgent, BrokeredRequester


def _responder(agent_id: str, broker: BrokerServer, **kw: object) -> BrokeredAgent:
    return BrokeredAgent(
        agent_id,
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        timeout=5.0,
        **kw,  # type: ignore[arg-type]
    )


def test_request_returns_reply(broker: BrokerServer) -> None:
    def handle(request: Request) -> Message:
        return Response.to(request, body={"reply": "hello from responder"})

    responder = _responder("responder", broker, on_request=handle)
    requester = BrokeredRequester(
        "requester",
        "responder",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
    )
    with responder:
        result = requester.request({"ping": 1})
    assert result == "hello from responder"


def test_request_falls_back_when_no_reply_key(broker: BrokerServer) -> None:
    def handle(request: Request) -> Message:
        return Response.to(request, body={"other": "no reply key"})

    responder = _responder("responder", broker, on_request=handle)
    requester = BrokeredRequester(
        "requester",
        "responder",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        default_reply="custom fallback",
    )
    with responder:
        result = requester.request({"ping": 1})
    assert result == "custom fallback"


def test_request_raises_runtime_error_on_broker_error(broker: BrokerServer) -> None:
    def handle(request: Request) -> Message:
        return Error.to(request, code="boom", message="something exploded")

    responder = _responder("responder", broker, on_request=handle)
    requester = BrokeredRequester(
        "requester",
        "responder",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
    )
    with responder, pytest.raises(RuntimeError, match="responder"):
        requester.request({"ping": 1})
