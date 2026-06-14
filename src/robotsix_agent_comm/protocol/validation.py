"""Validation and protocol-version handling.

The :func:`validate` function checks an envelope (either a :class:`Message`
instance or its ``dict`` form) for structural correctness and the
correlation invariants of each message kind.

Version policy: only the ``major`` component of ``protocol_version`` is
compared against :data:`PROTOCOL_VERSION`. A mismatched major raises
:class:`UnsupportedVersionError`; a differing ``minor`` with the same
major is accepted, providing forward/backward minor-compatibility.
"""

from __future__ import annotations

from typing import Any

from .messages import PROTOCOL_VERSION, Message, MessageType

REQUIRED_ENVELOPE_FIELDS = (
    "message_id",
    "type",
    "protocol_version",
    "metadata",
    "body",
)


class ProtocolError(Exception):
    """Base class for all protocol-level errors."""


class ValidationError(ProtocolError):
    """Raised when a message fails structural or invariant validation."""


class UnsupportedVersionError(ProtocolError):
    """Raised when a message's protocol major version is incompatible."""


def _major(version: str) -> int:
    """Return the integer major component of a ``major.minor`` version."""
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"invalid protocol_version: {version!r}") from exc


def validate(message_or_dict: Message | dict[str, Any]) -> None:
    """Validate an envelope, raising on the first problem found.

    Raises:
        ValidationError: when a required field is missing or mistyped, the
            ``type`` is unknown, or a correlation invariant is broken.
        UnsupportedVersionError: when the major version does not match.
    """
    if isinstance(message_or_dict, Message):
        data: dict[str, Any] = {
            "message_id": message_or_dict.message_id,
            "type": message_or_dict.type,
            "protocol_version": message_or_dict.protocol_version,
            "metadata": {
                "sender": message_or_dict.metadata.sender,
                "recipient": message_or_dict.metadata.recipient,
                "timestamp": message_or_dict.metadata.timestamp,
                "extra": message_or_dict.metadata.extra,
            },
            "body": message_or_dict.body,
            "correlation_id": message_or_dict.correlation_id,
        }
    elif isinstance(message_or_dict, dict):
        data = message_or_dict
        missing = [f for f in REQUIRED_ENVELOPE_FIELDS if f not in data]
        if missing:
            raise ValidationError(f"missing envelope fields: {missing}")
    else:
        raise ValidationError(
            f"cannot validate object of type {type(message_or_dict).__name__}"
        )

    message_id = data["message_id"]
    if not isinstance(message_id, str) or not message_id:
        raise ValidationError("message_id must be a non-empty string")

    try:
        message_type = MessageType(data["type"])
    except ValueError as exc:
        raise ValidationError(f"unknown message type: {data['type']!r}") from exc

    protocol_version = data["protocol_version"]
    if not isinstance(protocol_version, str):
        raise ValidationError("protocol_version must be a string")
    if _major(protocol_version) != _major(PROTOCOL_VERSION):
        raise UnsupportedVersionError(
            f"unsupported protocol major version: {protocol_version!r} "
            f"(expected major {PROTOCOL_VERSION})"
        )

    metadata = data["metadata"]
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a mapping")
    sender = metadata.get("sender")
    if not isinstance(sender, str) or not sender:
        raise ValidationError("metadata.sender must be a non-empty string")
    timestamp = metadata.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValidationError("metadata.timestamp must be a non-empty string")

    if not isinstance(data["body"], dict):
        raise ValidationError("body must be a mapping")

    correlation_id = data.get("correlation_id")
    if correlation_id is not None and not isinstance(correlation_id, str):
        raise ValidationError("correlation_id must be a string or None")

    if message_type is MessageType.RESPONSE and correlation_id is None:
        raise ValidationError("RESPONSE requires a correlation_id")
    if message_type is MessageType.REQUEST and correlation_id is not None:
        raise ValidationError("REQUEST must not carry a correlation_id")
    if message_type is MessageType.NOTIFICATION and correlation_id is not None:
        raise ValidationError("NOTIFICATION must not carry a correlation_id")
