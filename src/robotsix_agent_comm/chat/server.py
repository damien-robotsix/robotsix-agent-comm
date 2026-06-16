"""Chat SSE server — ASGI application and entry point.

Exposes an LLM agent to human users via HTTP + Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from robotsix_agent_comm.config import Settings
from robotsix_agent_comm.llm import Agent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSE wire-format constants — single source of truth for tests and consumers.
# ---------------------------------------------------------------------------

SSE_DONE_SENTINEL = "[DONE]"
SSE_ERROR_SENTINEL = "[ERROR]"
SSE_CONTENT_TYPE = "text/event-stream"


class ChatAgent(Protocol):
    """Structural interface for an agent that streams LLM responses.

    Any object whose ``stream(message)`` method returns an
    ``AsyncIterator[str]`` satisfies this protocol — no subclassing
    required.  (An ``async def`` generator method naturally returns an
    async iterator, so real implementations just write ``async def
    stream(self, message: str) -> AsyncIterator[str]:`` with ``yield``.)
    """

    def stream(self, message: str) -> AsyncIterator[str]:
        """Yield tokens from the LLM in response to ``message``."""
        ...


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def health_endpoint(request: Request) -> JSONResponse:
    """Liveness probe — returns 200 ``{"status": "ok"}``."""
    return JSONResponse({"status": "ok"})


async def chat_endpoint(
    request: Request,
) -> JSONResponse | StreamingResponse:
    """Accept a chat message and stream the agent's response as SSE."""
    agent: ChatAgent = request.app.state.agent

    # -- parse & validate JSON body ---------------------------------------
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)

    message = body.get("message")
    if not message or not isinstance(message, str):
        return JSONResponse(
            {"error": "missing or invalid 'message' field"}, status_code=400
        )

    # -- SSE async generator ----------------------------------------------

    async def sse_stream() -> AsyncIterator[bytes]:
        try:
            async for token in agent.stream(message):
                yield f"data: {token}\n\n".encode()
            yield f"data: {SSE_DONE_SENTINEL}\n\n".encode()
        except asyncio.CancelledError:
            logger.debug("SSE stream cancelled (client disconnect)")
        except Exception as exc:
            logger.exception("Agent stream error")
            yield f"data: {SSE_ERROR_SENTINEL} {exc}\n\n".encode()

    return StreamingResponse(
        sse_stream(),
        media_type=SSE_CONTENT_TYPE,
        headers={"Content-Type": SSE_CONTENT_TYPE},
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return JSON for unmatched routes instead of plain text."""
    return JSONResponse({"error": "not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Application factory & entry point
# ---------------------------------------------------------------------------


def create_app(agent: ChatAgent) -> Starlette:
    """Return a Starlette ASGI app wired to ``agent``.

    The returned app is a fully-initialised ASGI application that can be
    mounted directly in tests via ``httpx.ASGITransport`` or passed to
    ``uvicorn.run()``.
    """
    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/chat", chat_endpoint, methods=["POST"]),
    ]
    app = Starlette(
        routes=routes,
        exception_handlers={404: not_found_handler},
    )
    app.state.agent = agent
    return app


def run_server(
    agent: ChatAgent,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the chat SSE server on ``host:port``.

    Blocks until the process is interrupted (uvicorn handles
    ``SIGINT`` / ``SIGTERM``).
    """
    import uvicorn

    app = create_app(agent)
    uvicorn.run(app, host=host, port=port)


class LLMChatAgent:
    """Adapter that wraps ``llm.Agent`` to satisfy the :class:`ChatAgent` protocol."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def stream(self, message: str) -> AsyncIterator[str]:
        async for token in self._agent.run(message):
            yield token


def create_agent_from_settings(
    instruction: str, settings: Settings | None = None
) -> LLMChatAgent:
    """Build an :class:`LLMChatAgent` wired from *settings*.

    When *settings* is ``None``, ``Settings.from_env()`` is called to
    load configuration from the environment / ``.env`` file.
    """
    if settings is None:
        settings = Settings.from_env()

    agent = Agent(
        instruction=instruction,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )
    return LLMChatAgent(agent)


def run_server_from_config(agent: ChatAgent | None = None) -> None:
    """Start the chat SSE server using ``Settings.from_env()`` for configuration.

    Reads ``SERVER_HOST``, ``SERVER_PORT``, ``LOG_LEVEL``, and LLM
    settings (``LLM_API_KEY``, ``LLM_MODEL``, ``LLM_BASE_URL``) from the
    environment (with ``.env`` support), configures Python logging, builds
    a default :class:`LLMChatAgent` when *agent* is ``None``, and then
    delegates to :func:`run_server`.
    """
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    if agent is None:
        agent = create_agent_from_settings(
            instruction="You are a helpful assistant.", settings=settings
        )
    run_server(agent, host=settings.server_host, port=settings.server_port)
