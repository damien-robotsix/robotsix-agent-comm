"""Unit tests for the :class:`Router` recipient-routing logic."""

from __future__ import annotations

from typing import Any

import pytest

from robotsix_agent_comm.protocol import (
    Message,
    MessageType,
    Metadata,
    Request,
    Response,
)
from robotsix_agent_comm.transport import (
    AgentNotFoundError,
    DeliveryError,
    Endpoint,
    Registry,
    RetryPolicy,
    Router,
)
from robotsix_agent_comm.transport.errors import TransportError


class _FakeClient:
    """Test double for :class:`Transport` that records send calls."""

    def __init__(self, response: Message | None = None) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def send(
        self, message: Message, endpoint: Endpoint, *, timeout: float
    ) -> Message | None:
        self.calls.append(
            {"message": message, "endpoint": endpoint, "timeout": timeout}
        )
        return self._response

    def health_check(self, endpoint: Endpoint, *, timeout: float) -> bool:
        return True  # pragma: no cover — not exercised by Router


class _FailingClient:
    """Test double that always raises :class:`TransportError`."""

    def __init__(self, error: TransportError | None = None) -> None:
        self._error = error or TransportError("boom")
        self.attempts = 0

    def send(
        self, message: Message, endpoint: Endpoint, *, timeout: float
    ) -> Message | None:
        self.attempts += 1
        raise self._error

    def health_check(self, endpoint: Endpoint, *, timeout: float) -> bool:
        return True  # pragma: no cover — not exercised by Router


def _endpoint(agent_id: str = "agent-a") -> Endpoint:
    return Endpoint(agent_id=agent_id, host="127.0.0.1", port=8001)


def _registry(*endpoints: Endpoint) -> Registry:
    reg = Registry()
    for ep in endpoints:
        reg.register(ep)
    return reg


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=2, base_delay=0.1, max_delay=1.0, backoff_factor=2.0
    )


def _message(recipient: str = "agent-a") -> Message:
    return Request(
        metadata=Metadata.create(sender="agent-b", recipient=recipient),
        body={"action": "ping"},
    )


# ---------------------------------------------------------------------------
# Successful route
# ---------------------------------------------------------------------------


def test_route_successful_request_response() -> None:
    """A request is delivered and the response is returned."""
    reply = Response.to(
        _message(recipient="agent-a"),
        body={"echo": "pong"},
    )
    client = _FakeClient(response=reply)
    registry = _registry(_endpoint("agent-a"))
    router = Router(registry, client, _retry_policy())

    result = router.route(_message(recipient="agent-a"))

    assert result is reply
    assert len(client.calls) == 1
    assert client.calls[0]["endpoint"].agent_id == "agent-a"
    assert client.calls[0]["message"].metadata.recipient == "agent-a"


def test_route_notification_returns_none() -> None:
    """A fire-and-forget Notification returns None from the client."""
    client = _FakeClient(response=None)
    registry = _registry(_endpoint("alice"))
    router = Router(registry, client, _retry_policy())

    notification = Message(
        type=MessageType.NOTIFICATION,
        metadata=Metadata.create(sender="bob", recipient="alice"),
    )
    result = router.route(notification)

    assert result is None
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Missing / unknown recipient
# ---------------------------------------------------------------------------


def test_route_missing_recipient_raises_agent_not_found() -> None:
    """A message with an empty recipient raises AgentNotFoundError."""
    client = _FakeClient()
    registry = Registry()
    router = Router(registry, client, _retry_policy())

    msg = _message(recipient="")
    msg.metadata.recipient = ""

    with pytest.raises(AgentNotFoundError, match="no recipient"):
        router.route(msg)


def test_route_none_recipient_raises_agent_not_found() -> None:
    """A message with recipient=None raises AgentNotFoundError."""
    client = _FakeClient()
    registry = Registry()
    router = Router(registry, client, _retry_policy())

    msg = _message(recipient="")
    msg.metadata.recipient = None

    with pytest.raises(AgentNotFoundError, match="no recipient"):
        router.route(msg)


def test_route_unknown_recipient_raises_from_registry() -> None:
    """When the registry has no entry, AgentNotFoundError propagates."""
    client = _FakeClient()
    registry = _registry(_endpoint("known"))
    router = Router(registry, client, _retry_policy())

    with pytest.raises(AgentNotFoundError):
        router.route(_message(recipient="unknown"))


# ---------------------------------------------------------------------------
# Timeout override
# ---------------------------------------------------------------------------


def test_route_default_timeout() -> None:
    """When no timeout override is given, the Router's default is used."""
    client = _FakeClient()
    registry = _registry(_endpoint("agent-a"))
    router = Router(registry, client, _retry_policy(), timeout=12.0)

    router.route(_message(recipient="agent-a"))

    assert client.calls[0]["timeout"] == 12.0


def test_route_timeout_override() -> None:
    """An explicit timeout on route() overrides the Router's default."""
    client = _FakeClient()
    registry = _registry(_endpoint("agent-a"))
    router = Router(registry, client, _retry_policy(), timeout=5.0)

    router.route(_message(recipient="agent-a"), timeout=3.5)

    assert client.calls[0]["timeout"] == 3.5


def test_route_explicit_none_timeout_uses_default() -> None:
    """Passing timeout=None preserves the Router's configured default."""
    client = _FakeClient()
    registry = _registry(_endpoint("agent-a"))
    router = Router(registry, client, _retry_policy(), timeout=7.5)

    router.route(_message(recipient="agent-a"), timeout=None)

    assert client.calls[0]["timeout"] == 7.5


# ---------------------------------------------------------------------------
# Delivery failure (retry exhausted)
# ---------------------------------------------------------------------------


def test_route_delivery_failure_exhausts_retries() -> None:
    """When the client always fails, retries are exhausted and
    DeliveryError is raised."""
    error = TransportError("connection refused")
    client = _FailingClient(error=error)
    registry = _registry(_endpoint("agent-a"))
    policy = _retry_policy()
    router = Router(registry, client, policy)

    with pytest.raises(DeliveryError) as excinfo:
        router.route(_message(recipient="agent-a"))

    assert client.attempts == policy.max_attempts
    assert excinfo.value.cause is error
    assert excinfo.value.__cause__ is error
