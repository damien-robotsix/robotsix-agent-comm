"""Message model for the agent-communication protocol.

This module defines the in-memory message envelope and its four concrete
kinds (:class:`Request`, :class:`Response`, :class:`Error`,
:class:`Notification`) plus the supporting :class:`Metadata` structure and
the :class:`MessageType` discriminator.

Everything here is stdlib-only (``dataclasses``, ``enum``, ``uuid``,
``datetime``) per the fleet's stdlib-first convention; serialization and
validation live in sibling modules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION: str = "1.0"
"""Semantic ``major.minor`` version of the wire protocol."""


def new_message_id() -> str:
    """Return a fresh, unique message identifier."""
    return uuid.uuid4().hex


class MessageType(StrEnum):
    """Discriminator describing the kind of a :class:`Message`."""

    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"
    NOTIFICATION = "notification"


@dataclass(kw_only=True)
class Metadata:
    """Descriptive and routing metadata attached to every message.

    The structure is intentionally extensible: ``extra`` carries any
    additional, application-defined metadata without changing the schema.
    """

    sender: str
    recipient: str | None = None
    timestamp: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls, sender: str, recipient: str | None = None, **extra: Any
    ) -> Metadata:
        """Build metadata, stamping ``timestamp`` with the current UTC time."""
        return cls(
            sender=sender,
            recipient=recipient,
            timestamp=datetime.now(UTC).isoformat(),
            extra=dict(extra),
        )


@dataclass(kw_only=True)
class Message:
    """Base envelope shared by every protocol message.

    The class is ``kw_only`` so the mix of defaulted and non-defaulted
    fields does not depend on declaration order.
    """

    type: MessageType
    metadata: Metadata
    message_id: str = field(default_factory=new_message_id)
    protocol_version: str = PROTOCOL_VERSION
    body: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass(kw_only=True)
class Request(Message):
    """A request message that expects a matching :class:`Response`."""

    type: MessageType = field(default=MessageType.REQUEST, init=False)
    correlation_id: str | None = field(default=None, init=False)


@dataclass(kw_only=True)
class Response(Message):
    """A response answering a previously received :class:`Request`."""

    type: MessageType = field(default=MessageType.RESPONSE, init=False)

    def __post_init__(self) -> None:
        """Enforce that a response always references its request."""
        if self.correlation_id is None:
            raise ValueError("Response requires a correlation_id")

    @classmethod
    def to(
        cls,
        request: Message,
        *,
        body: dict[str, Any] | None = None,
        sender: str | None = None,
        **extra: Any,
    ) -> Response:
        """Build a response correlated to ``request`` with swapped routing.

        The response's ``correlation_id`` is set to the request's
        ``message_id`` and the sender/recipient are swapped.
        """
        reply_sender = sender if sender is not None else request.metadata.recipient
        if reply_sender is None:
            reply_sender = request.metadata.sender
        metadata = Metadata.create(
            sender=reply_sender,
            recipient=request.metadata.sender,
            **extra,
        )
        return cls(
            metadata=metadata,
            body=dict(body) if body is not None else {},
            correlation_id=request.message_id,
        )


def error_body(code: str, message: str, **details: Any) -> dict[str, Any]:
    """Return a structured error body with a ``code`` and ``message``."""
    return {"code": code, "message": message, **details}


@dataclass(kw_only=True)
class Error(Message):
    """An error message, optionally correlated to a failing request."""

    type: MessageType = field(default=MessageType.ERROR, init=False)

    @classmethod
    def to(
        cls,
        request: Message,
        *,
        code: str,
        message: str,
        sender: str | None = None,
        **details: Any,
    ) -> Error:
        """Build an error correlated to ``request`` with swapped routing."""
        reply_sender = sender if sender is not None else request.metadata.recipient
        if reply_sender is None:
            reply_sender = request.metadata.sender
        metadata = Metadata.create(
            sender=reply_sender,
            recipient=request.metadata.sender,
        )
        return cls(
            metadata=metadata,
            body=error_body(code, message, **details),
            correlation_id=request.message_id,
        )


@dataclass(kw_only=True)
class Notification(Message):
    """A fire-and-forget notification with no correlation."""

    type: MessageType = field(default=MessageType.NOTIFICATION, init=False)
    correlation_id: str | None = field(default=None, init=False)
