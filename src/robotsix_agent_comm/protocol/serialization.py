"""JSON serialization for protocol messages.

Format is JSON via the stdlib :mod:`json` module (stdlib-first; protobuf
and third-party codecs are out of scope). The wire form is a UTF-8 ``str``:
:func:`serialize` returns ``str`` and :func:`deserialize` accepts ``str``.

:func:`deserialize` always runs :func:`validate`, so malformed input raises
a clear error instead of producing a half-built object.
"""

from __future__ import annotations

import json
from typing import Any

from .messages import (
    Error,
    Message,
    MessageType,
    Metadata,
    Notification,
    Request,
    Response,
)
from .validation import ValidationError, validate

_TYPE_TO_CLASS: dict[MessageType, type[Message]] = {
    MessageType.REQUEST: Request,
    MessageType.RESPONSE: Response,
    MessageType.ERROR: Error,
    MessageType.NOTIFICATION: Notification,
}


def to_dict(message: Message) -> dict[str, Any]:
    """Return a JSON-compatible ``dict`` representation of ``message``."""
    return {
        "message_id": message.message_id,
        "type": message.type.value,
        "protocol_version": message.protocol_version,
        "metadata": {
            "sender": message.metadata.sender,
            "recipient": message.metadata.recipient,
            "timestamp": message.metadata.timestamp,
            "extra": dict(message.metadata.extra),
        },
        "body": dict(message.body),
        "correlation_id": message.correlation_id,
    }


def from_dict(data: dict[str, Any]) -> Message:
    """Reconstruct a concrete :class:`Message` from its ``dict`` form.

    Validation runs first; malformed input raises :class:`ValidationError`
    (or :class:`UnsupportedVersionError`).
    """
    validate(data)
    message_type = MessageType(data["type"])
    raw_metadata = data["metadata"]
    metadata = Metadata(
        sender=raw_metadata["sender"],
        recipient=raw_metadata.get("recipient"),
        timestamp=raw_metadata.get("timestamp", ""),
        extra=dict(raw_metadata.get("extra", {})),
    )
    cls = _TYPE_TO_CLASS[message_type]
    kwargs: dict[str, Any] = {
        "message_id": data["message_id"],
        "metadata": metadata,
        "protocol_version": data["protocol_version"],
        "body": dict(data["body"]),
    }
    if message_type in (MessageType.RESPONSE, MessageType.ERROR):
        kwargs["correlation_id"] = data.get("correlation_id")
    return cls(**kwargs)


def serialize(message: Message) -> str:
    """Serialize ``message`` to a JSON ``str``."""
    return json.dumps(to_dict(message))


def deserialize(raw: str) -> Message:
    """Parse and validate a JSON ``str`` into a concrete :class:`Message`."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError(f"invalid JSON payload: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("serialized message must be a JSON object")
    return from_dict(data)
