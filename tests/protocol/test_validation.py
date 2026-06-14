"""Tests for envelope validation and version handling."""

from __future__ import annotations

from typing import Any

import pytest

from robotsix_agent_comm.protocol import (
    Metadata,
    Request,
    UnsupportedVersionError,
    ValidationError,
    to_dict,
    validate,
)


def _valid_dict() -> dict[str, Any]:
    return to_dict(
        Request(
            metadata=Metadata.create(sender="alice", recipient="bob"),
            body={"action": "ping"},
        )
    )


def test_valid_message_passes() -> None:
    validate(_valid_dict())


def test_valid_message_instance_passes() -> None:
    validate(Request(metadata=Metadata.create(sender="alice")))


def test_unknown_type_raises() -> None:
    data = _valid_dict()
    data["type"] = "bogus"
    with pytest.raises(ValidationError):
        validate(data)


def test_missing_envelope_field_raises() -> None:
    data = _valid_dict()
    del data["metadata"]
    with pytest.raises(ValidationError):
        validate(data)


def test_missing_sender_raises() -> None:
    data = _valid_dict()
    data["metadata"]["sender"] = ""
    with pytest.raises(ValidationError):
        validate(data)


def test_missing_timestamp_raises() -> None:
    data = _valid_dict()
    data["metadata"]["timestamp"] = ""
    with pytest.raises(ValidationError):
        validate(data)


def test_response_without_correlation_raises() -> None:
    data = _valid_dict()
    data["type"] = "response"
    data["correlation_id"] = None
    with pytest.raises(ValidationError):
        validate(data)


def test_notification_with_correlation_raises() -> None:
    data = _valid_dict()
    data["type"] = "notification"
    data["correlation_id"] = "abc"
    with pytest.raises(ValidationError):
        validate(data)


def test_request_with_correlation_raises() -> None:
    data = _valid_dict()
    data["correlation_id"] = "abc"
    with pytest.raises(ValidationError):
        validate(data)


def test_major_version_mismatch_raises() -> None:
    data = _valid_dict()
    data["protocol_version"] = "2.0"
    with pytest.raises(UnsupportedVersionError):
        validate(data)


def test_same_major_differing_minor_passes() -> None:
    data = _valid_dict()
    data["protocol_version"] = "1.7"
    validate(data)
