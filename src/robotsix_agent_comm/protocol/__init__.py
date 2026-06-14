"""Agent-communication message protocol.

Public surface for constructing, serializing, parsing, and validating
protocol messages. The layer is stdlib-only and free of network/transport
concerns (those land in later phases).
"""

from __future__ import annotations

from .messages import (
    PROTOCOL_VERSION,
    Error,
    Message,
    MessageType,
    Metadata,
    Notification,
    Request,
    Response,
    error_body,
    new_message_id,
)
from .serialization import deserialize, from_dict, serialize, to_dict
from .validation import (
    ProtocolError,
    UnsupportedVersionError,
    ValidationError,
    validate,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Error",
    "Message",
    "MessageType",
    "Metadata",
    "Notification",
    "ProtocolError",
    "Request",
    "Response",
    "UnsupportedVersionError",
    "ValidationError",
    "deserialize",
    "error_body",
    "from_dict",
    "new_message_id",
    "serialize",
    "to_dict",
    "validate",
]
