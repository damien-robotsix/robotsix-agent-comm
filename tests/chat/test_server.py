"""Tests for the chat SSE server."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from robotsix_agent_comm.chat.server import (
    SSE_CONTENT_TYPE,
    SSE_DONE_SENTINEL,
    SSE_ERROR_SENTINEL,
    create_app,
)


class MockAgent:
    """A :class:`ChatAgent` that yields a fixed list of tokens."""

    def __init__(
        self,
        tokens: list[str] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.tokens = tokens or ["Hello", " ", "world!"]
        self.error = error
        self.called_with: str | None = None

    async def stream(self, message: str) -> AsyncIterator[str]:
        self.called_with = message
        if self.error is not None:
            raise self.error
        for token in self.tokens:
            yield token


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Chat endpoint — SSE streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_streams_tokens() -> None:
    agent = MockAgent(tokens=["Hello", " ", "world!"])
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"] == SSE_CONTENT_TYPE

    text = response.text
    # SSE uses \n\n as event delimiter.  Split on that, then extract the
    # token from each "data: ..." block.
    events = [e for e in text.split("\n\n") if e]
    data_lines: list[str] = []
    for e in events:
        if e.startswith("data: "):
            data_lines.append(e[len("data: ") :])
        elif e == "data:":
            data_lines.append("")

    assert len(data_lines) >= 3
    assert data_lines[0] == "Hello"
    assert data_lines[1] == " "
    assert data_lines[2] == "world!"
    assert data_lines[-1] == SSE_DONE_SENTINEL


@pytest.mark.asyncio
async def test_chat_endpoint_passes_message_to_agent() -> None:
    agent = MockAgent(tokens=["ok"])
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/chat", json={"message": "hello world"})

    assert agent.called_with == "hello world"


@pytest.mark.asyncio
async def test_chat_endpoint_sends_done_at_end() -> None:
    agent = MockAgent(tokens=["one", "two"])
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"message": "x"})

    assert response.text.endswith(
        f"data: {SSE_DONE_SENTINEL}\n\n"
    ) or response.text.endswith(f"data: {SSE_DONE_SENTINEL}\r\n\r\n")


# ---------------------------------------------------------------------------
# Chat endpoint — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_missing_message_field() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={})

    assert response.status_code == 400
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_chat_endpoint_message_not_a_string() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"message": 123})

    assert response.status_code == 400
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_chat_endpoint_invalid_json() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat", content=b"not json", headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 400
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_chat_endpoint_empty_message_string() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"message": ""})

    assert response.status_code == 400
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_chat_endpoint_agent_raises() -> None:
    error_agent = MockAgent(error=RuntimeError("LLM went boom"))
    app = create_app(error_agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert SSE_CONTENT_TYPE in response.headers["content-type"]
    assert f"data: {SSE_ERROR_SENTINEL}" in response.text
    assert "LLM went boom" in response.text
    assert SSE_DONE_SENTINEL not in response.text


# ---------------------------------------------------------------------------
# Unknown routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_route_returns_404_json() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert data == {"error": "not found"}


@pytest.mark.asyncio
async def test_wrong_method_on_known_route_returns_405() -> None:
    agent = MockAgent()
    app = create_app(agent)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # POST /health is not a valid endpoint — Starlette returns 405.
        response = await client.post("/health")

    assert response.status_code == 405
