"""Unit tests for :class:`LifecycleServer` handler methods and init behaviour."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from robotsix_agent_comm.lifecycle.config import LifecycleConfig
from robotsix_agent_comm.lifecycle.server import LifecycleServer
from robotsix_agent_comm.lifecycle.tracing import LifecycleTracing
from robotsix_agent_comm.protocol import Metadata, Request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(sender: str = "test-sender") -> Request:
    """Return a minimal :class:`Request` for handler testing."""
    return Request(metadata=Metadata.create(sender=sender))


def _make_config(**overrides: Any) -> LifecycleConfig:
    """Return a :class:`LifecycleConfig` with sensible test defaults."""
    defaults: dict[str, Any] = {
        "agent_id": "test-lifecycle",
        "broker_host": "127.0.0.1",
        "broker_port": 9999,
        "broker_scheme": "http",
        "broker_token": None,
        "broker_tls_ca": None,
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
        "langfuse_host": None,
    }
    defaults.update(overrides)
    return LifecycleConfig(**defaults)


def _make_server(
    tracing: LifecycleTracing | MagicMock | None = None,
    config: LifecycleConfig | None = None,
) -> LifecycleServer:
    """Build a :class:`LifecycleServer` with network-creation mocked out.

    Patches ``create_transport_pair`` in the brokered module so the real
    ``__init__`` chain can run (setting up ``_extra_handlers``, ``agent_id``,
    etc.) without opening any sockets.
    """
    if config is None:
        config = _make_config()
    if tracing is None:
        tracing = LifecycleTracing()  # no-op mode

    mock_agent = MagicMock()
    mock_transport = MagicMock()
    with patch(
        "robotsix_agent_comm.sdk.brokered.create_transport_pair",
        return_value=(mock_agent, mock_transport),
    ):
        server = LifecycleServer(config=config, tracing=tracing)  # type: ignore[arg-type]
    return server


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for :class:`LifecycleServer.__init__`."""

    @staticmethod
    def _patch_transport_and_build(
        config: LifecycleConfig | None = None,
        tracing: LifecycleTracing | MagicMock | None = None,
    ) -> LifecycleServer:
        """Patch ``create_transport_pair`` and construct a LifecycleServer."""
        if config is None:
            config = _make_config()
        if tracing is None:
            tracing = LifecycleTracing()
        mock_agent = MagicMock()
        mock_transport = MagicMock()
        with patch(
            "robotsix_agent_comm.sdk.brokered.create_transport_pair",
            return_value=(mock_agent, mock_transport),
        ):
            return LifecycleServer(config=config, tracing=tracing)  # type: ignore[arg-type]

    def test_stores_tracing_instance(self) -> None:
        """tracing is stored as ``self.tracing``."""
        tracing = MagicMock(spec=LifecycleTracing)
        server = _make_server(tracing=tracing)
        assert server.tracing is tracing

    def test_registers_status_handler(self) -> None:
        """__init__ calls ``register_handler("status", ...)``."""
        with patch.object(LifecycleServer, "register_handler") as mock_register:
            server = self._patch_transport_and_build()
            mock_register.assert_any_call("status", server.handle_status)

    def test_registers_lifecycle_handler(self) -> None:
        """__init__ calls ``register_handler("lifecycle", ...)``."""
        with patch.object(LifecycleServer, "register_handler") as mock_register:
            server = self._patch_transport_and_build()
            mock_register.assert_any_call("lifecycle", server.handle_lifecycle)

    def test_super_init_receives_config_fields(self) -> None:
        """Parent ``BrokeredResponder.__init__`` is called with config-derived kwargs."""
        config = _make_config(
            agent_id="custom-id",
            broker_host="broker.local",
            broker_port=1234,
            broker_scheme="https",
            broker_token="tok-abc",
            broker_tls_ca="/path/to/ca.pem",
        )
        with patch(
            "robotsix_agent_comm.lifecycle.server.BrokeredResponder.__init__",
            return_value=None,
        ) as mock_super_init:
            # We must ALSO patch register_handler since super().__init__
            # is mocked (so _extra_handlers is never created) but
            # LifecycleServer.__init__ still calls register_handler.
            with patch.object(LifecycleServer, "register_handler"):
                LifecycleServer(config=config, tracing=LifecycleTracing())  # type: ignore[arg-type]
            mock_super_init.assert_called_once_with(
                agent_id="custom-id",
                broker_host="broker.local",
                broker_port=1234,
                broker_scheme="https",
                broker_token="tok-abc",
                tls_ca="/path/to/ca.pem",
            )


# ---------------------------------------------------------------------------
# handle_monitor tests
# ---------------------------------------------------------------------------


class TestHandleMonitor:
    """Tests for :meth:`LifecycleServer.handle_monitor`."""

    def test_returns_expected_shape(self) -> None:
        """Result dict has ``status``, ``agent_id``, ``tracing_enabled``."""
        server = _make_server()
        request = _make_request()
        result = server.handle_monitor(request, {})

        assert result == {
            "status": "ok",
            "agent_id": "test-lifecycle",
            "tracing_enabled": False,
        }

    def test_tracing_disabled_no_trace_call(self) -> None:
        """When tracing is disabled, no trace context is entered."""
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = False
        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_monitor(request, {})

        assert result["tracing_enabled"] is False
        tracing.trace.assert_not_called()

    def test_tracing_enabled_enters_trace_context(self) -> None:
        """When tracing is enabled, the handler wraps the result in a trace span."""
        mock_span = MagicMock()
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = True
        tracing.trace.return_value.__enter__.return_value = mock_span

        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_monitor(request, {})

        assert result["tracing_enabled"] is True
        tracing.trace.assert_called_once_with("monitor")
        mock_span.event.assert_called_once_with("monitor-check")

    def test_passes_empty_params_through(self) -> None:
        """A request with empty ``params`` dict still returns a valid result."""
        server = _make_server()
        request = _make_request()
        result = server.handle_monitor(request, {})
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# handle_status tests
# ---------------------------------------------------------------------------


class TestHandleStatus:
    """Tests for :meth:`LifecycleServer.handle_status`."""

    def test_returns_expected_shape(self) -> None:
        """Result dict has ``status``, ``agent_id``, ``tracing_enabled``."""
        server = _make_server()
        request = _make_request()
        result = server.handle_status(request, {})

        assert result == {
            "status": "ok",
            "agent_id": "test-lifecycle",
            "tracing_enabled": False,
        }

    def test_tracing_disabled_no_trace_call(self) -> None:
        """When tracing is disabled, no trace context is entered."""
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = False
        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_status(request, {})
        assert result["tracing_enabled"] is False
        tracing.trace.assert_not_called()

    def test_tracing_enabled_enters_trace_context(self) -> None:
        """When tracing is enabled, the handler wraps the result in a trace span."""
        mock_span = MagicMock()
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = True
        tracing.trace.return_value.__enter__.return_value = mock_span

        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_status(request, {})

        assert result["tracing_enabled"] is True
        tracing.trace.assert_called_once_with("status")
        mock_span.event.assert_called_once_with("status-check")


# ---------------------------------------------------------------------------
# handle_lifecycle tests
# ---------------------------------------------------------------------------


class TestHandleLifecycle:
    """Tests for :meth:`LifecycleServer.handle_lifecycle`."""

    def test_returns_acknowledged_with_command_and_service(self) -> None:
        """Result dict includes ``result``, ``command``, and ``service`` keys."""
        server = _make_server()
        request = _make_request()
        result = server.handle_lifecycle(
            request, {"command": "restart", "service": "nginx"}
        )

        assert result == {
            "result": "acknowledged",
            "command": "restart",
            "service": "nginx",
        }

    def test_missing_command_defaults_to_unknown(self) -> None:
        """When ``params`` lacks ``command``, the result uses ``"unknown"``."""
        server = _make_server()
        request = _make_request()
        result = server.handle_lifecycle(request, {"service": "nginx"})

        assert result == {
            "result": "acknowledged",
            "command": "unknown",
            "service": "nginx",
        }

    def test_missing_service_defaults_to_unknown(self) -> None:
        """When ``params`` lacks ``service``, the result uses ``"unknown"``."""
        server = _make_server()
        request = _make_request()
        result = server.handle_lifecycle(request, {"command": "restart"})

        assert result == {
            "result": "acknowledged",
            "command": "restart",
            "service": "unknown",
        }

    def test_empty_params_defaults_both_to_unknown(self) -> None:
        """When ``params`` is empty, both command and service default to ``"unknown"``."""
        server = _make_server()
        request = _make_request()
        result = server.handle_lifecycle(request, {})

        assert result == {
            "result": "acknowledged",
            "command": "unknown",
            "service": "unknown",
        }

    def test_tracing_disabled_no_trace_call(self) -> None:
        """When tracing is disabled, no trace context is entered."""
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = False
        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_lifecycle(request, {"command": "stop", "service": "api"})
        assert result["command"] == "stop"
        tracing.trace.assert_not_called()

    def test_tracing_enabled_enters_trace_with_metadata(self) -> None:
        """When tracing is enabled, the handler wraps result in a trace span with metadata."""
        mock_span = MagicMock()
        tracing = MagicMock(spec=LifecycleTracing)
        tracing.enabled = True
        tracing.trace.return_value.__enter__.return_value = mock_span

        server = _make_server(tracing=tracing)
        request = _make_request()

        result = server.handle_lifecycle(
            request, {"command": "reload", "service": "gateway"}
        )

        assert result["result"] == "acknowledged"
        tracing.trace.assert_called_once_with("lifecycle")
        mock_span.event.assert_called_once_with(
            "lifecycle-command",
            metadata={"command": "reload", "service": "gateway"},
        )
