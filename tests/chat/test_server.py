"""Tests for the chat SSE server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from robotsix_agent_comm.chat.server import (
    SSE_CONTENT_TYPE,
    SSE_DONE_SENTINEL,
    SSE_ERROR_SENTINEL,
    LLMChatAgent,
    create_agent_from_settings,
    create_app,
    run_server_from_config,
)
from robotsix_agent_comm.config import Settings


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
# LLMChatAgent adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_chat_agent_streams_tokens() -> None:
    """``LLMChatAgent`` delegates ``stream()`` to the wrapped ``Agent.run()``."""
    mock_agent = MagicMock()

    async def fake_run(message: str) -> AsyncIterator[str]:
        for token in ["Hi", " ", "there"]:
            yield token

    mock_agent.run = fake_run

    adapter = LLMChatAgent(mock_agent)
    tokens = [token async for token in adapter.stream("hello")]

    assert tokens == ["Hi", " ", "there"]


# ---------------------------------------------------------------------------
# create_agent_from_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_from_settings_explicit() -> None:
    """``create_agent_from_settings`` wires LLM fields from a ``Settings`` object."""
    settings = Settings(
        llm_api_key="sk-from-settings",
        llm_model="gpt-5",
        llm_base_url="https://custom.example.com",
    )

    with patch("robotsix_agent_comm.chat.server.Agent") as MockAgent:
        mock_agent_instance = MockAgent.return_value
        result = create_agent_from_settings("Be concise.", settings=settings)

        MockAgent.assert_called_once_with(
            instruction="Be concise.",
            api_key="sk-from-settings",
            model="gpt-5",
            base_url="https://custom.example.com",
        )
        assert isinstance(result, LLMChatAgent)
        assert result._agent is mock_agent_instance


@pytest.mark.asyncio
async def test_create_agent_from_settings_uses_from_env_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_agent_from_settings`` loads from the environment when
    *settings* is ``None``."""
    monkeypatch.setenv("LLM_API_KEY", "sk-env-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8080/v1")

    with patch("robotsix_agent_comm.chat.server.Agent") as MockAgent:
        result = create_agent_from_settings("Helpful bot.")

        MockAgent.assert_called_once_with(
            instruction="Helpful bot.",
            api_key="sk-env-test",
            model="gpt-4.1",
            base_url="http://localhost:8080/v1",
        )
        assert isinstance(result, LLMChatAgent)


# ---------------------------------------------------------------------------
# run_server_from_config — LLM wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_from_config_creates_agent_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_server_from_config()`` with no *agent* creates an
    ``LLMChatAgent`` from ``Settings``."""
    monkeypatch.setenv("LLM_API_KEY", "sk-run-server-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("SERVER_PORT", "8080")

    with (
        patch("robotsix_agent_comm.chat.server.Agent") as MockAgent,
        patch("robotsix_agent_comm.chat.server.run_server") as mock_run_server,
    ):
        run_server_from_config()

        MockAgent.assert_called_once_with(
            instruction="You are a helpful assistant.",
            api_key="sk-run-server-test",
            model="gpt-4o",
            base_url=None,  # empty string → None via Settings
        )
        # The agent passed to run_server is an LLMChatAgent wrapping
        # the constructed Agent.
        call_args = mock_run_server.call_args
        passed_agent = call_args[0][0]
        assert isinstance(passed_agent, LLMChatAgent)
        assert passed_agent._agent is MockAgent.return_value
        assert call_args[1] == {"host": "127.0.0.1", "port": 8080}


@pytest.mark.asyncio
async def test_run_server_from_config_passes_explicit_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_server_from_config(agent)`` forwards *agent* to
    ``run_server`` without creating a new one."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    mock_agent = MagicMock()

    with patch("robotsix_agent_comm.chat.server.run_server") as mock_run_server:
        run_server_from_config(mock_agent)

        mock_run_server.assert_called_once()
        passed_agent = mock_run_server.call_args[0][0]
        assert passed_agent is mock_agent


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
