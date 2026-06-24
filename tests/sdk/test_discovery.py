"""Tests for the discovery API (``discover_agents`` / ``AgentInfo``)."""

from __future__ import annotations

from typing import Any

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Request
from robotsix_agent_comm.sdk import discover_agents
from robotsix_agent_comm.sdk.responder import BrokeredResponder
from robotsix_agent_comm.transport.errors import TransportError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _responder_started(
    agent_id: str, broker: BrokerServer, *, broker_token: str | None = None
) -> BrokeredResponder:
    """Create and start a plain :class:`BrokeredResponder` against *broker*."""
    responder = BrokeredResponder(
        agent_id,
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=broker_token,
        timeout=5.0,
    )
    responder.start()
    return responder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_discover_empty(broker: BrokerServer) -> None:
    """``discover_agents`` against an empty broker returns an empty list."""
    result = discover_agents(
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
    )
    assert result == []


def test_discover_single_responder(broker: BrokerServer) -> None:
    """One responder with only builtins: verify its capabilities."""
    responder = _responder_started("alpha", broker)
    try:
        result = discover_agents(
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="http",
        )
        assert len(result) == 1
        info = result[0]
        assert info.agent_id == "alpha"
        assert info.supported_kinds == sorted(["config-get", "config-set", "monitor"])
    finally:
        responder.stop()


def test_discover_multiple_responders(broker: BrokerServer) -> None:
    """Two responders — one with a custom handler — each advertise correctly."""
    # Responder A: builtins only.
    r_a = _responder_started("agent-a", broker)

    # Responder B: builtins + one custom handler registered *before* start.
    r_b = BrokeredResponder(
        "agent-b",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        timeout=5.0,
    )

    @r_b.register_handler("custom-echo")
    def _custom(_request: Request, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    r_b.start()

    try:
        result = discover_agents(
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="http",
        )
        assert len(result) == 2

        by_id = {info.agent_id: info for info in result}

        # Agent A: exactly the three builtins.
        assert by_id["agent-a"].supported_kinds == sorted(
            ["config-get", "config-set", "monitor"]
        )

        # Agent B: builtins + custom-echo.
        assert by_id["agent-b"].supported_kinds == sorted(
            ["config-get", "config-set", "custom-echo", "monitor"]
        )
    finally:
        r_a.stop()
        r_b.stop()


def test_discover_auth() -> None:
    """Authorized caller succeeds, missing token raises ``TransportError``."""
    server = BrokerServer(
        host="127.0.0.1",
        port=0,
        agent_tokens={"agent-a": "tok-a", "caller": "tok-c"},
    )
    server.start()
    try:
        # Register one agent under auth.
        r = _responder_started("agent-a", server, broker_token="tok-a")
        try:
            # Authorized caller sees exactly one entry.
            result = discover_agents(
                broker_host=server.host,
                broker_port=server.port,
                broker_scheme="http",
                broker_token="tok-c",
            )
            assert len(result) == 1
            assert result[0].agent_id == "agent-a"

            # Unauthenticated caller gets a TransportError.
            with pytest.raises(TransportError):
                discover_agents(
                    broker_host=server.host,
                    broker_port=server.port,
                    broker_scheme="http",
                )
        finally:
            r.stop()
    finally:
        server.stop()
