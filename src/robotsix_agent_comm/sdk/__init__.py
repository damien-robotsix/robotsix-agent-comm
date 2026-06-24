"""High-level agent communication SDK.

Composes the ``protocol`` and ``transport`` public APIs into a single,
synchronous :class:`Agent` client so a developer can register, send, and
receive messages in a few lines. The layer is stdlib-only and consumes the
lower layers unchanged.
"""

from __future__ import annotations

from ..transport.endpoints import AgentInfo
from .agent import Agent, NotificationHandler, RequestHandler
from .brokered import BrokeredAgent
from .brokered_request import BrokeredRequester
from .discovery import discover_agents
from .reply import reply_text
from .responder import BrokeredResponder

__all__ = [
    "Agent",
    "AgentInfo",
    "BrokeredAgent",
    "BrokeredRequester",
    "BrokeredResponder",
    "NotificationHandler",
    "RequestHandler",
    "discover_agents",
    "reply_text",
]
