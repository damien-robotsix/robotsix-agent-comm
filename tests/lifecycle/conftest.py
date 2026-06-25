"""Shared fixtures for lifecycle tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from robotsix_agent_comm.lifecycle import (
    DeploymentStore,
    LifecycleServer,
    MockBackend,
)


@pytest.fixture
def store() -> DeploymentStore:
    """Return a fresh in-memory DeploymentStore."""
    return DeploymentStore()


@pytest.fixture
def backend() -> MockBackend:
    """Return a fresh MockBackend."""
    return MockBackend()


@pytest.fixture
def server(
    backend: MockBackend, store: DeploymentStore
) -> Generator[LifecycleServer, None, None]:
    """Start a LifecycleServer on an OS-assigned port; stop on teardown."""
    srv = LifecycleServer(
        backend=backend,
        store=store,
        host="127.0.0.1",
        port=0,
        auth_token="test-token",
        health_timeout_seconds=0.1,
        health_interval_seconds=0.5,
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def server_no_auth(
    backend: MockBackend, store: DeploymentStore
) -> Generator[LifecycleServer, None, None]:
    """Start a LifecycleServer with auth disabled; stop on teardown."""
    srv = LifecycleServer(
        backend=backend,
        store=store,
        host="127.0.0.1",
        port=0,
        auth_token=None,
        health_timeout_seconds=0.1,
        health_interval_seconds=0.5,
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def base_url(server: LifecycleServer) -> str:
    """Return the base URL for the running server."""
    return f"http://{server.host}:{server.port}"


@pytest.fixture
def base_url_no_auth(server_no_auth: LifecycleServer) -> str:
    """Return the base URL for the no-auth server."""
    return f"http://{server_no_auth.host}:{server_no_auth.port}"
