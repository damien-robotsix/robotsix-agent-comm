"""Tests for JSON serialization round-trips and error handling."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.protocol import (
    Error,
    Message,
    MessageType,
    Metadata,
    Notification,
    Request,
    Response,
    ValidationError,
    deserialize,
    serialize,
)


def _request() -> Request:
    return Request(
        metadata=Metadata.create(sender="alice", recipient="bob", trace="abc"),
        body={"action": "ping", "args": [1, 2, 3]},
    )


def _messages() -> list[Message]:
    req = _request()
    return [
        req,
        Response.to(req, body={"pong": True}),
        Error.to(req, code="E_BAD", message="boom"),
        Notification(
            metadata=Metadata.create(sender="alice"),
            body={"event": "tick"},
        ),
    ]


@pytest.mark.parametrize("message", _messages())
def test_round_trip_preserves_message(message: Message) -> None:
    restored = deserialize(serialize(message))
    assert restored == message
    assert restored.type is message.type
    assert restored.metadata.extra == message.metadata.extra
    assert restored.protocol_version == message.protocol_version


def test_message_type_string_round_trips() -> None:
    req = _request()
    raw = serialize(req)
    assert '"type": "request"' in raw
    assert deserialize(raw).type is MessageType.REQUEST


def test_malformed_json_raises() -> None:
    with pytest.raises(ValidationError):
        deserialize("{not json")


def test_missing_fields_raise() -> None:
    with pytest.raises(ValidationError):
        deserialize('{"type": "request"}')
