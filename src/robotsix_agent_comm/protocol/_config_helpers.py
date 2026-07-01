"""Shared helpers for parsing environment-variable based configuration.

Internal module — not part of the public protocol API.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol


def _parse_bool(raw: str) -> bool:
    """Accept ``1`` / ``true`` / ``yes`` (case-insensitive) as truthy."""
    return raw.strip().lower() in ("1", "true", "yes")


class _EnvGetter(Protocol):
    """Protocol for the closure returned by :func:`make_env_getter`."""

    def __call__(self, key: str, default: str = "") -> str: ...


def make_env_getter(
    env: Mapping[str, str] | None = None,
) -> _EnvGetter:
    """Return a ``_get(key, default="")`` closure bound to *env*.

    When *env* is ``None`` (the default) the closure reads from
    ``os.environ``.  Pass an explicit ``dict`` to keep tests isolated.
    """
    if env is None:
        env = os.environ

    def _get(key: str, default: str = "") -> str:
        return env.get(key, default)

    return _get
