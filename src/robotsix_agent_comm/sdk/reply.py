"""Pure, dependency-free reply-text extractor.

Consumers use :func:`reply_text` to extract a human-readable ``"reply"``
string from a brokered response body, with a configurable fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["reply_text"]


def reply_text(body: Any, *, default: str = "") -> str:
    """Extract a human-readable reply string from a response body.

    Returns the ``"reply"`` value from a mapping body (coerced to ``str`` if
    it is not already a string), falling back to ``default`` when *body* is
    not a mapping, has no ``"reply"`` key, or that value is ``None``/empty.
    """
    if isinstance(body, Mapping):
        reply = body.get("reply")
        if reply is not None and reply != "":
            if isinstance(reply, str):
                return reply
            return str(reply)
    return default
