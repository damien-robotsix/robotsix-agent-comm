"""Shared enum types for broker traffic disposition and agent status.

These enums provide a single source of truth for string values used across
the broker server, dashboard JS, and transport layer.
"""

from __future__ import annotations

from enum import StrEnum


class TrafficDisposition(StrEnum):
    """Outcome of a broker message-routing attempt.

    Used in traffic records (:meth:`_BrokerServer._record_traffic`) and in
    the monitoring dashboard to colour-code traffic rows.
    """

    QUEUED = "queued"
    """Message was enqueued for a mailbox (pull) recipient."""

    REJECTED = "rejected"
    """Message was rejected before routing (auth mismatch, missing recipient)."""

    ROUTED = "routed"
    """Message was forwarded to the recipient's transport endpoint."""

    UNKNOWN_RECIPIENT = "unknown_recipient"
    """Recipient was not found in the broker's agent registry."""


class AgentStatus(StrEnum):
    """Liveness status of a registered agent.

    Computed by :meth:`_BrokerServer._build_agent_entry` from heartbeat
    age and TTL. Surfaced on ``GET /agents`` and the monitoring dashboard.
    """

    ACTIVE = "active"
    """Agent heartbeat is current (within its TTL)."""

    STALE = "stale"
    """Agent heartbeat has exceeded its TTL."""

    UNKNOWN = "unknown"
    """Agent has no heartbeat data (fresh registration or stale status
    before the first heartbeat arrives)."""
