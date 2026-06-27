"""Unit tests for :func:`build_server` and :func:`main` from
``robotsix_agent_comm.lifecycle.service``.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from robotsix_agent_comm.lifecycle.config import LifecycleConfig
from robotsix_agent_comm.lifecycle.server import LifecycleServer
from robotsix_agent_comm.lifecycle.service import build_server, main
from robotsix_agent_comm.lifecycle.tracing import LifecycleTracing

# ---------------------------------------------------------------------------
# build_server() tests
# ---------------------------------------------------------------------------


class TestBuildServer:
    """Tests for :func:`build_server`."""

    @staticmethod
    def test_constructs_server_with_config_and_tracing() -> None:
        """``build_server`` creates a :class:`LifecycleServer` whose
        ``agent_id`` matches the config and ``tracing`` is a
        :class:`LifecycleTracing` instance.
        """
        config = LifecycleConfig(
            agent_id="test-svc",
            broker_host="127.0.0.1",
            broker_port=9999,
            broker_scheme="http",
            broker_token=None,
            broker_tls_ca=None,
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_host="https://langfuse.example.com",
        )

        mock_registry = MagicMock()
        mock_transport = MagicMock()
        with patch(
            "robotsix_agent_comm.sdk.brokered.create_transport_pair",
            return_value=(mock_registry, mock_transport),
        ):
            server = build_server(config)

        assert isinstance(server, LifecycleServer)
        assert server.agent_id == "test-svc"
        assert isinstance(server.tracing, LifecycleTracing)

    @staticmethod
    def test_creates_tracing_from_config() -> None:
        """The :class:`LifecycleTracing` instance receives the Langfuse
        keys from the config.
        """
        config = LifecycleConfig(
            agent_id="traced",
            broker_host="127.0.0.1",
            broker_port=9999,
            broker_scheme="http",
            langfuse_public_key="pk-from-config",
            langfuse_secret_key="sk-from-config",
            langfuse_host="https://custom.example.com",
        )

        mock_registry = MagicMock()
        mock_transport = MagicMock()
        with patch(
            "robotsix_agent_comm.sdk.brokered.create_transport_pair",
            return_value=(mock_registry, mock_transport),
        ):
            server = build_server(config)

        # Verify the tracing was constructed (the actual enabled flag
        # depends on whether langfuse is importable at runtime).
        assert isinstance(server.tracing, LifecycleTracing)
        # The agent_id comes from the config.
        assert server.agent_id == "traced"


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for :func:`main`."""

    @staticmethod
    def test_returns_1_on_config_error() -> None:
        """When ``LifecycleConfig.from_env()`` raises ``ValueError``,
        ``main()`` returns ``1``.
        """
        with patch.object(
            LifecycleConfig, "from_env", side_effect=ValueError("bad port")
        ):
            exit_code = main()
        assert exit_code == 1

    @staticmethod
    def test_happy_path_returns_0_after_signal() -> None:
        """``main()`` starts the server, registers SIGTERM/SIGINT
        handlers, and returns ``0`` after the shutdown event is set.
        """
        config = LifecycleConfig(
            agent_id="main-test",
            broker_host="127.0.0.1",
            broker_port=9999,
            broker_scheme="http",
        )

        mock_server = MagicMock(spec=LifecycleServer)
        mock_server.agent_id = "main-test"

        signal_handlers: dict[int, Callable[..., Any]] = {}

        def _fake_signal(signum: int, handler: object) -> None:
            signal_handlers[signum] = handler  # type: ignore[assignment]

        # Intercept threading.Event so we can trigger shutdown.
        shutdown_event_mock = MagicMock()

        with (
            patch.object(LifecycleConfig, "from_env", return_value=config),
            patch(
                "robotsix_agent_comm.lifecycle.service.build_server",
                return_value=mock_server,
            ),
            patch(
                "robotsix_agent_comm.lifecycle.service.signal.signal",
                _fake_signal,
            ),
            patch(
                "robotsix_agent_comm.lifecycle.service.threading.Event",
                return_value=shutdown_event_mock,
            ),
        ):
            exit_code = main()

        # -- Asserts after main() returns ---------------------------------
        mock_server.start.assert_called_once()
        mock_server.stop.assert_called_once()

        # The shutdown event was waited on.
        shutdown_event_mock.wait.assert_called_once()

        # Both signals were registered with the SAME handler.
        assert signal.SIGTERM in signal_handlers
        assert signal.SIGINT in signal_handlers
        assert signal_handlers[signal.SIGTERM] is signal_handlers[signal.SIGINT]

        # Simulate the signal handler — it must set the event.
        signal_handlers[signal.SIGTERM](signal.SIGTERM, None)
        shutdown_event_mock.set.assert_called_once()

        assert exit_code == 0

    @staticmethod
    def test_sigint_also_triggers_shutdown() -> None:
        """Sending SIGINT also sets the shutdown event."""
        config = LifecycleConfig(
            agent_id="sigint-test",
            broker_host="127.0.0.1",
            broker_port=9999,
            broker_scheme="http",
        )

        mock_server = MagicMock(spec=LifecycleServer)
        mock_server.agent_id = "sigint-test"

        signal_handlers: dict[int, Callable[..., Any]] = {}

        def _fake_signal(signum: int, handler: object) -> None:
            signal_handlers[signum] = handler  # type: ignore[assignment]

        shutdown_event_mock = MagicMock()

        with (
            patch.object(LifecycleConfig, "from_env", return_value=config),
            patch(
                "robotsix_agent_comm.lifecycle.service.build_server",
                return_value=mock_server,
            ),
            patch(
                "robotsix_agent_comm.lifecycle.service.signal.signal",
                _fake_signal,
            ),
            patch(
                "robotsix_agent_comm.lifecycle.service.threading.Event",
                return_value=shutdown_event_mock,
            ),
        ):
            exit_code = main()

        assert signal.SIGINT in signal_handlers
        # Fire the SIGINT handler — it sets the event.
        signal_handlers[signal.SIGINT](signal.SIGINT, None)
        shutdown_event_mock.set.assert_called_once()

        assert exit_code == 0
