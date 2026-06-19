"""Broker self-heal: a polling pull agent's mailbox is auto-(re)created when the
broker has no record of it — e.g. after a restart cleared the in-memory registry
(image update, cert renewal). Consumers recover without a manual restart.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.sdk.agent import Agent
from robotsix_agent_comm.transport import AgentNotFoundError, Endpoint
from robotsix_agent_comm.transport.brokered import (
    NetworkedBrokerTransport,
    create_transport_pair,
)
from robotsix_agent_comm.transport.registry import Registry


@pytest.fixture
def broker() -> Generator[BrokerServer, None, None]:
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_poll_auto_registers_unknown_mailbox(broker: BrokerServer) -> None:
    state = broker._server
    transport = NetworkedBrokerTransport(broker.host, broker.port)

    assert "ghost" not in state.mailboxes
    # A poll for an unregistered agent returns empty AND registers its mailbox.
    assert transport.receive("ghost", wait=0.1, timeout=5.0) == []
    assert "ghost" in state.mailboxes
    state.registry.lookup("ghost")  # would raise AgentNotFoundError if missing


def test_delivery_works_after_auto_register(broker: BrokerServer) -> None:
    transport = NetworkedBrokerTransport(broker.host, broker.port)
    # Simulate the listener's recv loop re-registering itself via a poll.
    transport.receive("listener", wait=0.1, timeout=5.0)

    sender_reg, sender_tr = create_transport_pair(
        "brokered", broker_host=broker.host, broker_port=broker.port
    )
    sender = Agent("sender", sender_reg, transport=sender_tr, pull=True, timeout=5.0)
    with sender:
        # No AgentNotFoundError: the auto-registered mailbox exists.
        sender.send_notification("listener", {"event": "spike"})

    msgs = transport.receive("listener", wait=1.0, timeout=5.0)
    assert len(msgs) == 1
    assert msgs[0].body == {"event": "spike"}


def test_self_heal_after_registry_reset(broker: BrokerServer) -> None:
    state = broker._server
    reg, _tr = create_transport_pair(
        "brokered", broker_host=broker.host, broker_port=broker.port
    )
    reg.register(Endpoint(agent_id="responder", host="mailbox", port=0, mailbox=True))
    state.registry.lookup("responder")

    # Simulate a broker restart wiping all in-memory state.
    with state.mailbox_cond:
        state.mailboxes.clear()
    state.registry = Registry()
    state.ttl_seconds.clear()
    state.last_heartbeat.clear()
    with pytest.raises(AgentNotFoundError):
        state.registry.lookup("responder")

    # The agent's recv loop keeps polling — its next poll re-registers it.
    NetworkedBrokerTransport(broker.host, broker.port).receive(
        "responder", wait=0.1, timeout=5.0
    )
    state.registry.lookup("responder")  # restored, no exception
