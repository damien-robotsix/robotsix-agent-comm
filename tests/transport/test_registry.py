"""Tests for the in-memory endpoint registry."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.transport import AgentNotFoundError, Endpoint, Registry


def _endpoint(agent_id: str = "agent-a", port: int = 8001) -> Endpoint:
    return Endpoint(agent_id=agent_id, host="127.0.0.1", port=port)


def test_register_and_lookup() -> None:
    registry = Registry()
    endpoint = _endpoint()
    registry.register(endpoint)
    assert registry.lookup("agent-a") is endpoint


def test_lookup_unknown_raises() -> None:
    registry = Registry()
    with pytest.raises(AgentNotFoundError):
        registry.lookup("nobody")


def test_unregister_removes_entry() -> None:
    registry = Registry()
    registry.register(_endpoint())
    registry.unregister("agent-a")
    with pytest.raises(AgentNotFoundError):
        registry.lookup("agent-a")


def test_unregister_unknown_raises() -> None:
    registry = Registry()
    with pytest.raises(AgentNotFoundError):
        registry.unregister("nobody")


def test_list_agents_snapshot() -> None:
    registry = Registry()
    registry.register(_endpoint("agent-a", 8001))
    registry.register(_endpoint("agent-b", 8002))
    agents = {endpoint.agent_id for endpoint in registry.list_agents()}
    assert agents == {"agent-a", "agent-b"}


def test_endpoint_urls() -> None:
    endpoint = Endpoint(agent_id="x", host="host", port=9000)
    assert endpoint.url == "http://host:9000/messages"
    assert endpoint.health_url == "http://host:9000/health"
