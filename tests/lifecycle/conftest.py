"""Shared fixtures for the lifecycle test suite."""

from __future__ import annotations

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.lifecycle.config import LifecycleConfig
from robotsix_agent_comm.lifecycle.server import LifecycleServer
from robotsix_agent_comm.lifecycle.tracing import LifecycleTracing


def create_lifecycle_server(broker: BrokerServer) -> LifecycleServer:
    """Start a LifecycleServer registered with the test *broker*.

    The caller is responsible for calling ``server.stop()`` after the test.
    """
    config = LifecycleConfig(
        agent_id="lifecycle-server",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        broker_tls_ca=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        langfuse_host=None,
    )
    tracing = LifecycleTracing()
    server = LifecycleServer(config=config, tracing=tracing)
    server.start()
    return server
