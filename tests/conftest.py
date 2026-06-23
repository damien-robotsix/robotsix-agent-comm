"""Suite-wide shared fixtures for the robotsix-agent-comm test suite."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Message
from robotsix_agent_comm.transport import TransportServer
from tests.helpers import _echo_handler


@pytest.fixture
def broker() -> Generator[BrokerServer, None, None]:
    """Start a plain (no TLS, no auth) BrokerServer on an ephemeral port."""
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def agent_server() -> Generator[tuple[TransportServer, list[Message]], None, None]:
    """Start a TransportServer with an echo handler on an ephemeral port."""
    received: list[Message] = []
    server = TransportServer(_echo_handler(received), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server, received
    finally:
        server.stop()
