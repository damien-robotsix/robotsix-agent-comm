"""Config-contract base types for the broker config-get / config-set protocol.

This module provides shared, stdlib-only types that agent repos can use
to implement the ``config-get`` and ``config-set`` request kinds without
duplicating the core error class, secret redaction, or the structural
interface contract.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Protocol, runtime_checkable

from robotsix_agent_comm.errors import RobotsixAgentCommError

REDACTED_SENTINEL = "***"
"""Sentinel value substituted for secret config values in snapshots."""

_type = type  #: Alias to avoid shadowing the builtin in :class:`SettableKey`.


class ConfigContractError(RobotsixAgentCommError):
    """Error raised during config-get or config-set processing.

    Mirrors the ``(code, message, **details)`` shape of
    :class:`robotsix_agent_comm.protocol.Error` so callers can
    construct a protocol error body directly from an instance.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        """Create an error with a machine-readable *code* and human *message*."""
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details
        super().__init__(message)

    def __str__(self) -> str:
        """Format as ``[code] message``."""
        return f"[{self.code}] {self.message}"


class SettableKey(NamedTuple):
    """Metadata for a single config key exposed via config-get/config-set.

    Attributes:
        key: Dotted-path key string (e.g. ``"server.log_level"``).
        type: Human-readable type label (e.g. ``"string"``, ``"integer"``).
        python_type: Python type used for validation.
        path: Key split into path segments (e.g. ``["server", "log_level"]``).
    """

    key: str
    type: str
    python_type: _type
    path: list[str]


class SecretRedactor:
    """Stateless helper for secret detection and redaction in config snapshots."""

    REDACTED_SENTINEL = REDACTED_SENTINEL

    @staticmethod
    def is_secret(key: str, patterns: frozenset[str]) -> bool:
        """Return ``True`` if *key* matches any of the *patterns*.

        A key is considered secret when any pattern string appears as a
        substring of the key.
        """
        return any(pattern in key for pattern in patterns)

    @staticmethod
    def redact_value(key: str, value: Any, patterns: frozenset[str]) -> Any:
        """Return ``REDACTED_SENTINEL`` if *key* is secret, else *value*."""
        if SecretRedactor.is_secret(key, patterns):
            return REDACTED_SENTINEL
        return value


@runtime_checkable
class ConfigContract(Protocol):
    """Structural interface for config-get / config-set implementors.

    Agent repos implement this protocol on their config-contract class
    to provide a consistent API for the broker's config request kinds.
    """

    def get_snapshot(self, settings: Any) -> dict[str, Any]:
        """Return a flat dotted-path→value view of *settings*."""
        ...

    def describe(self) -> dict[str, Any]:
        """Return per-key metadata (type, settable, secret)."""
        ...

    def validate_update(
        self, settings: Any, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Validate *updates* without applying them; return errors or ``None``."""
        ...

    def apply_update(self, settings: Any, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply *updates* to *settings*; return audit dict."""
        ...
