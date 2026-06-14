"""Tests for message construction and correlation invariants."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.protocol import (
    Error,
    MessageType,
    Metadata,
    Notification,
    Request,
    Response,
    new_message_id,
)


def _request() -> Request:
    return Request(
        metadata=Metadata.create(sender="alice", recipient="bob"),
        body={"action": "ping"},
    )


def test_new_message_id_is_unique() -> None:
    ids = {new_message_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_metadata_create_stamps_timestamp() -> None:
    meta = Metadata.create(sender="alice", recipient="bob", priority="high")
    assert meta.timestamp
    assert meta.sender == "alice"
    assert meta.recipient == "bob"
    assert meta.extra == {"priority": "high"}


def test_request_invariants() -> None:
    req = _request()
    assert req.type is MessageType.REQUEST
    assert req.message_id
    assert req.correlation_id is None


def test_notification_invariants() -> None:
    note = Notification(metadata=Metadata.create(sender="alice"))
    assert note.type is MessageType.NOTIFICATION
    assert note.correlation_id is None


def test_response_to_copies_correlation_and_swaps_routing() -> None:
    req = _request()
    resp = Response.to(req, body={"pong": True})
    assert resp.type is MessageType.RESPONSE
    assert resp.correlation_id == req.message_id
    assert resp.metadata.sender == "bob"
    assert resp.metadata.recipient == "alice"
    assert resp.body == {"pong": True}


def test_response_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        Response(metadata=Metadata.create(sender="bob"))


def test_error_to_copies_correlation_and_builds_body() -> None:
    req = _request()
    err = Error.to(req, code="E_BAD", message="boom", detail=42)
    assert err.type is MessageType.ERROR
    assert err.correlation_id == req.message_id
    assert err.metadata.sender == "bob"
    assert err.metadata.recipient == "alice"
    assert err.body == {"code": "E_BAD", "message": "boom", "detail": 42}


def test_unsolicited_error_allows_no_correlation() -> None:
    err = Error(
        metadata=Metadata.create(sender="watchdog"),
        body={"code": "E_OOM", "message": "out of memory"},
    )
    assert err.correlation_id is None
