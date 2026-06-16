"""Chat SSE server — HTTP + Server-Sent Events bridge for human-to-agent chat.

Exposes an LLM agent (represented by the :class:`ChatAgent` protocol) via
``POST /chat`` (SSE stream) and ``GET /health`` (liveness probe). Built on
Starlette so it can be tested with ``httpx.ASGITransport`` without binding a
real port.
"""

from __future__ import annotations

from .server import (
    SSE_CONTENT_TYPE,
    SSE_DONE_TYPE,
    SSE_ERROR_TYPE,
    SSE_TOKEN_TYPE,
    ChatAgent,
    create_app,
    run_server,
)

__all__ = [
    "ChatAgent",
    "SSE_CONTENT_TYPE",
    "SSE_DONE_TYPE",
    "SSE_ERROR_TYPE",
    "SSE_TOKEN_TYPE",
    "create_app",
    "run_server",
]
