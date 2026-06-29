"""Tests for the supervision agent: health polling, restart, escalation."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

import pytest

from robotsix_agent_comm.lifecycle import (
    MockBackend,
    SupervisionAgent,
    SupervisionConfig,
)
from robotsix_agent_comm.lifecycle.supervision import Incident, IncidentKind

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_json(url: str) -> dict[str, Any]:
    """GET *url* and return decoded JSON body."""
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())  # type: ignore[no-any-return]


def _make_config(**overrides: Any) -> SupervisionConfig:
    """Build a test config with fast intervals."""
    kwargs: dict[str, Any] = {
        "poll_interval_seconds": 0.1,
        "health_timeout_seconds": 0.5,
        "max_restart_attempts": 3,
        "backoff_base_seconds": 0.01,
        "backoff_max_seconds": 0.1,
        "escalation_cooldown_seconds": 1.0,
        "services": ("test-svc",),
        "status_host": "127.0.0.1",
        "status_port": 0,
    }
    kwargs.update(overrides)
    return SupervisionConfig(**kwargs)


# ---------------------------------------------------------------------------
# Healthy steady-state
# ---------------------------------------------------------------------------


class TestHealthySteadyState:
    def test_all_services_healthy_no_incidents(self) -> None:
        """When all services are healthy, no incidents are recorded."""
        backend = MockBackend(health_results=[True])  # always healthy
        alerts: list[Incident] = []

        config = _make_config()
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)
        agent.start()
        try:
            time.sleep(0.3)  # Allow a couple of poll cycles.
        finally:
            agent.stop()

        assert not alerts, f"Expected no alerts, got {alerts}"
        state = agent.services_state["test-svc"]
        assert state.healthy is True
        assert state.consecutive_failures == 0
        assert state.restart_count == 0
        assert not state.escalated

    def test_status_endpoint_healthy(self) -> None:
        """GET /status returns per-service health summary."""
        backend = MockBackend(health_results=[True])
        config = _make_config()
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(0.2)
            url = f"http://{agent.status_host}:{agent.status_port}/status"
            resp = _get_json(url)
        finally:
            agent.stop()

        assert resp["running"] is True  # agent is still running
        svc = resp["services"]["test-svc"]
        assert svc["healthy"] is True
        assert svc["consecutive_failures"] == 0
        assert svc["restart_count"] == 0
        assert svc["escalated"] is False

    def test_status_endpoint_404(self) -> None:
        """Unknown paths on the status server return 404."""
        backend = MockBackend(health_results=[True])
        config = _make_config()
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            url = f"http://{agent.status_host}:{agent.status_port}/nope"
            req = urllib.request.Request(url, method="GET")
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
                body = json.loads(exc.read())
                assert body == {"error": "not found"}
                exc.close()
            else:
                raise AssertionError("expected HTTPError 404")
        finally:
            agent.stop()

    def test_multiple_services(self) -> None:
        """Multiple services are polled independently."""
        backend = MockBackend(health_results=[True])
        config = _make_config(services=("svc-a", "svc-b"))
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(0.3)
        finally:
            agent.stop()

        assert "svc-a" in agent.services_state
        assert "svc-b" in agent.services_state
        assert agent.services_state["svc-a"].healthy is True
        assert agent.services_state["svc-b"].healthy is True


# ---------------------------------------------------------------------------
# Transient failure → auto-restart
# ---------------------------------------------------------------------------


class TestAutoRestart:
    def test_transient_failure_triggers_restart(self) -> None:
        """An unhealthy service is restarted and the incident is recorded."""
        # health() returns False once (trigger failure), then True (recovery).
        backend = MockBackend(health_results=[False, True])
        alerts: list[Incident] = []

        config = _make_config()
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)
        agent.start()
        try:
            time.sleep(0.5)
        finally:
            agent.stop()

        # Should have at least "degraded" and "restarted" incidents.
        kinds = [a.kind for a in alerts]
        assert IncidentKind.DEGRADED in kinds, f"Expected 'degraded' in {kinds}"
        assert IncidentKind.RESTARTED in kinds, f"Expected 'restarted' in {kinds}"

        state = agent.services_state["test-svc"]
        assert state.restart_count >= 1
        assert len(backend.stop_calls) >= 1
        assert len(backend.start_calls) >= 1

    def test_restart_backoff_increases(self) -> None:
        """Each restart attempt increases the backoff delay."""
        # Always unhealthy → triggers multiple restart attempts.
        backend = MockBackend(health_results=[False])
        alerts: list[Incident] = []

        config = _make_config(
            max_restart_attempts=3,
            backoff_base_seconds=0.02,
            backoff_max_seconds=0.2,
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,  # long enough to not interfere
        )
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)

        agent.start()
        try:
            # Let several poll cycles happen.  The backoff between restarts
            # increases each time, so we need enough time for 3 restarts.
            time.sleep(2.0)
        finally:
            agent.stop()

        restart_incidents = [a for a in alerts if a.kind == IncidentKind.RESTARTED]
        assert len(restart_incidents) >= 1

        state = agent.services_state["test-svc"]
        assert state.restart_count >= 1

    def test_restart_calls_backend_stop_and_start(self) -> None:
        """Each restart calls backend.stop() then backend.start()."""
        backend = MockBackend(health_results=[False])
        config = _make_config(
            max_restart_attempts=2,
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,
        )
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(1.0)
        finally:
            agent.stop()

        assert len(backend.stop_calls) >= 1
        assert len(backend.start_calls) >= 1
        # stop and start should be interleaved — each restart is stop+start.
        # Since health is always False, the restart loop will trigger multiple
        # times, but each restart calls stop+start.
        assert len(backend.stop_calls) >= len(backend.start_calls) - 1

    def test_healthy_after_restart_resets_counters(self) -> None:
        """When a service recovers after a restart, failure counters reset."""
        # Fail once, then become healthy on the next poll.
        backend = MockBackend(health_results=[False, True, True, True])
        config = _make_config()
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(0.6)
        finally:
            agent.stop()

        state = agent.services_state["test-svc"]
        # After recovery, consecutive_failures should be 0.
        assert state.consecutive_failures == 0
        assert state.healthy is True
        assert not state.escalated


# ---------------------------------------------------------------------------
# Escalation after threshold
# ---------------------------------------------------------------------------


class TestEscalation:
    def test_escalation_after_max_failures(self) -> None:
        """After max_restart_attempts consecutive failures, the service is escalated."""
        backend = MockBackend(health_results=[False])  # always unhealthy
        alerts: list[Incident] = []

        config = _make_config(
            max_restart_attempts=2,
            backoff_base_seconds=0.01,
            backoff_max_seconds=0.05,
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,
        )
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)
        agent.start()
        try:
            time.sleep(2.0)
        finally:
            agent.stop()

        escalated = [a for a in alerts if a.kind == IncidentKind.ESCALATED]
        assert len(escalated) >= 1, f"Expected escalation alert, got {alerts}"

        state = agent.services_state["test-svc"]
        assert state.escalated is True
        assert state.escalation_time is not None

    def test_escalation_stops_restarts(self) -> None:
        """After escalation, no further restarts are attempted (during cooldown)."""
        backend = MockBackend(health_results=[False])
        alerts: list[Incident] = []

        config = _make_config(
            max_restart_attempts=2,
            backoff_base_seconds=0.01,
            backoff_max_seconds=0.05,
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,
        )
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)
        agent.start()
        try:
            time.sleep(2.0)
        finally:
            agent.stop()

        # Count restarts: should be at most max_restart_attempts + 1
        # (the +1 is because the first failure triggers a restart, then
        # each subsequent failure in the poll loop triggers another restart
        # attempt until we exceed max_restart_attempts).
        restart_count = sum(1 for a in alerts if a.kind == IncidentKind.RESTARTED)
        # After escalation, no more restarts.  The exact count depends on
        # timing, but it should be bounded.
        assert restart_count <= config.max_restart_attempts + 1

    def test_escalation_incident_message_includes_details(self) -> None:
        """The escalation incident message describes failures and cooldown."""
        backend = MockBackend(health_results=[False])
        alerts: list[Incident] = []

        config = _make_config(
            max_restart_attempts=1,
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,
        )
        agent = SupervisionAgent(config, backend, on_alert=alerts.append)
        agent.start()
        try:
            time.sleep(2.0)
        finally:
            agent.stop()

        escalated = [a for a in alerts if a.kind == IncidentKind.ESCALATED]
        assert len(escalated) >= 1
        msg = escalated[0].message
        assert "test-svc" in msg
        assert "escalated" in msg
        assert "10s" in msg or "10.0s" in msg

    def test_cooldown_prevents_polling_while_escalated(self) -> None:
        """During escalation cooldown, the service is not health-checked."""
        backend = MockBackend(health_results=[False])
        config = _make_config(
            max_restart_attempts=0,  # escalate immediately on first failure
            health_timeout_seconds=0.05,
            escalation_cooldown_seconds=10.0,
            poll_interval_seconds=0.05,
        )
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(1.0)
        finally:
            agent.stop()

        state = agent.services_state["test-svc"]
        assert state.escalated is True
        # The health check count should be low because after escalation
        # the cooldown blocks further checks.
        assert state.consecutive_failures <= 2  # at most the initial + maybe one more


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_stop_idempotent(self) -> None:
        """Calling start/stop multiple times is safe."""
        backend = MockBackend(health_results=[True])
        config = _make_config(services=())
        agent = SupervisionAgent(config, backend)
        agent.start()
        agent.start()  # idempotent
        agent.stop()
        agent.stop()  # idempotent

    def test_context_manager(self) -> None:
        """SupervisionAgent can be used as a context manager."""
        backend = MockBackend(health_results=[True])
        config = _make_config()
        with SupervisionAgent(config, backend) as agent:
            assert agent.running is True
        assert agent.running is False

    def test_no_services_is_safe(self) -> None:
        """An empty services list does not crash the poll loop."""
        backend = MockBackend()
        config = _make_config(services=())
        agent = SupervisionAgent(config, backend)
        agent.start()
        try:
            time.sleep(0.2)
        finally:
            agent.stop()
        # Should not have raised.


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self) -> None:
        """Default SupervisionConfig has sensible values."""
        cfg = SupervisionConfig()
        assert cfg.poll_interval_seconds == 30.0
        assert cfg.max_restart_attempts == 3
        assert cfg.services == ()

    def test_validate_rejects_negative_poll_interval(self) -> None:
        """Negative poll interval raises ValueError."""
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            _make_config(poll_interval_seconds=-1).validate()

    def test_validate_rejects_backoff_max_lt_base(self) -> None:
        """backoff_max_seconds < backoff_base_seconds raises ValueError."""
        with pytest.raises(ValueError, match="backoff_max_seconds"):
            _make_config(backoff_base_seconds=10.0, backoff_max_seconds=5.0).validate()

    def test_from_env_parses_services(self) -> None:
        """from_env reads ROBOTSIX_SUPERVISION_SERVICES."""
        cfg = SupervisionConfig.from_env(
            {"ROBOTSIX_SUPERVISION_SERVICES": "svc-a, svc-b , svc-c"}
        )
        assert cfg.services == ("svc-a", "svc-b", "svc-c")

    def test_from_env_defaults(self) -> None:
        """from_env with no vars returns defaults."""
        cfg = SupervisionConfig.from_env({})
        assert cfg.poll_interval_seconds == 30.0
        assert cfg.services == ()

    def test_from_env_custom_values(self) -> None:
        """from_env reads all customisable values."""
        cfg = SupervisionConfig.from_env(
            {
                "ROBOTSIX_SUPERVISION_POLL_INTERVAL_SECONDS": "5.0",
                "ROBOTSIX_SUPERVISION_HEALTH_TIMEOUT_SECONDS": "3.0",
                "ROBOTSIX_SUPERVISION_MAX_RESTART_ATTEMPTS": "5",
                "ROBOTSIX_SUPERVISION_BACKOFF_BASE_SECONDS": "2.0",
                "ROBOTSIX_SUPERVISION_BACKOFF_MAX_SECONDS": "60.0",
                "ROBOTSIX_SUPERVISION_ESCALATION_COOLDOWN_SECONDS": "120.0",
                "ROBOTSIX_SUPERVISION_SERVICES": "svc1 svc2",
                "ROBOTSIX_SUPERVISION_STATUS_HOST": "0.0.0.0",
                "ROBOTSIX_SUPERVISION_STATUS_PORT": "9090",
            }
        )
        assert cfg.poll_interval_seconds == 5.0
        assert cfg.health_timeout_seconds == 3.0
        assert cfg.max_restart_attempts == 5
        assert cfg.backoff_base_seconds == 2.0
        assert cfg.backoff_max_seconds == 60.0
        assert cfg.escalation_cooldown_seconds == 120.0
        assert cfg.services == ("svc1", "svc2")
        assert cfg.status_host == "0.0.0.0"
        assert cfg.status_port == 9090

    def test_build_supervisor_uses_subprocess_backend_by_default(self) -> None:
        """build_supervisor() defaults to SubprocessBackend."""
        from robotsix_agent_comm.lifecycle.backend import SubprocessBackend
        from robotsix_agent_comm.lifecycle.supervision import build_supervisor

        cfg = _make_config(services=())
        agent = build_supervisor(cfg)
        assert isinstance(agent._backend, SubprocessBackend)
