"""Unit tests for lifecycle backends: SubprocessBackend, MockBackend, ABC."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from robotsix_agent_comm.lifecycle.backend import (
    LifecycleBackend,
    MockBackend,
    SubprocessBackend,
)

# ===========================================================================
# LifecycleBackend ABC
# ===========================================================================


class TestLifecycleBackendABC:
    """Verify the abstract interface contract."""

    def test_cannot_instantiate_abc_directly(self) -> None:
        """Instantiating the ABC directly should raise TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            LifecycleBackend()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_all_abstracts(self) -> None:
        """A subclass missing an abstract method cannot be instantiated."""

        class Partial(LifecycleBackend):
            health_results: list[bool] = []

            def start(self, service_name: str, version: str | None = None) -> None:
                pass

            # missing stop() and health()

        with pytest.raises(TypeError, match="abstract"):
            Partial()  # type: ignore[abstract]

    def test_subprocess_backend_is_a_lifecycle_backend(self) -> None:
        """SubprocessBackend is a valid LifecycleBackend subclass."""
        assert issubclass(SubprocessBackend, LifecycleBackend)
        backend = SubprocessBackend()
        assert isinstance(backend, LifecycleBackend)

    def test_mock_backend_is_a_lifecycle_backend(self) -> None:
        """MockBackend is a valid LifecycleBackend subclass."""
        assert issubclass(MockBackend, LifecycleBackend)
        backend = MockBackend()
        assert isinstance(backend, LifecycleBackend)


# ===========================================================================
# SubprocessBackend._compose_cmd()
# ===========================================================================


class TestSubprocessBackendComposeCmd:
    """Tests for _compose_cmd() composition."""

    def test_default_no_project_dir(self) -> None:
        """Without project_dir, returns just ['docker', 'compose']."""
        backend = SubprocessBackend()
        assert backend._compose_cmd() == ["docker", "compose"]

    def test_with_project_dir(self) -> None:
        """With project_dir, includes --project-directory flag."""
        backend = SubprocessBackend(project_dir="/opt/services")
        assert backend._compose_cmd() == [
            "docker",
            "compose",
            "--project-directory",
            "/opt/services",
        ]

    def test_with_none_explicit(self) -> None:
        """Explicit None behaves like the default (no flag)."""
        backend = SubprocessBackend(project_dir=None)
        assert backend._compose_cmd() == ["docker", "compose"]

    def test_with_empty_string(self) -> None:
        """Empty string is treated as a project_dir value (not None)."""
        backend = SubprocessBackend(project_dir="")
        assert backend._compose_cmd() == [
            "docker",
            "compose",
            "--project-directory",
            "",
        ]


# ===========================================================================
# SubprocessBackend.start()
# ===========================================================================


class TestSubprocessBackendStart:
    """Tests for SubprocessBackend.start()."""

    def test_start_default_command(self) -> None:
        """start() runs 'docker compose up -d <service>'."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["docker", "compose", "up", "-d", "my-service"]

    def test_start_passes_check_and_capture(self) -> None:
        """start() passes check=True, capture_output=True, text=True."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service")
            kwargs = mock_run.call_args[1]
            assert kwargs["check"] is True
            assert kwargs["capture_output"] is True
            assert kwargs["text"] is True

    def test_start_with_version_sets_env(self) -> None:
        """When version is provided, SERVICE_VERSION is injected into env."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service", version="2.3.1")
            env = mock_run.call_args[1]["env"]
            assert env["SERVICE_VERSION"] == "2.3.1"

    def test_start_without_version_has_no_service_version(self) -> None:
        """When version is None, SERVICE_VERSION is not in env."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service", version=None)
            env = mock_run.call_args[1]["env"]
            assert "SERVICE_VERSION" not in env

    def test_start_with_project_dir_includes_flag(self) -> None:
        """start() includes --project-directory when project_dir is set."""
        backend = SubprocessBackend(project_dir="/my/project")
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service")
            cmd = mock_run.call_args[0][0]
            assert "--project-directory" in cmd
            assert "/my/project" in cmd

    def test_start_does_not_mutate_os_environ(self) -> None:
        """start() copies os.environ; does not pollute the real environment."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.start("my-service", version="1.0")
            # The real environment must not acquire SERVICE_VERSION
            assert "SERVICE_VERSION" not in __import__("os").environ
            # But the mocked call received it
            assert mock_run.call_args[1]["env"]["SERVICE_VERSION"] == "1.0"


# ===========================================================================
# SubprocessBackend.stop()
# ===========================================================================


class TestSubprocessBackendStop:
    """Tests for SubprocessBackend.stop()."""

    def test_stop_default_command(self) -> None:
        """stop() runs 'docker compose stop <service>'."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.stop("my-service")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["docker", "compose", "stop", "my-service"]

    def test_stop_passes_check_and_capture(self) -> None:
        """stop() also uses check=True, capture_output=True, text=True."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            backend.stop("my-service")
            kwargs = mock_run.call_args[1]
            assert kwargs["check"] is True
            assert kwargs["capture_output"] is True
            assert kwargs["text"] is True

    def test_stop_with_project_dir_includes_flag(self) -> None:
        """stop() includes --project-directory when project_dir is set."""
        backend = SubprocessBackend(project_dir="/projects/app")
        with mock.patch("subprocess.run") as mock_run:
            backend.stop("my-service")
            cmd = mock_run.call_args[0][0]
            assert "--project-directory" in cmd
            assert "/projects/app" in cmd


# ===========================================================================
# SubprocessBackend.health()
# ===========================================================================


class TestSubprocessBackendHealth:
    """Tests for SubprocessBackend.health()."""

    def test_health_command(self) -> None:
        """health() runs 'docker compose ps --status running <service>'."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            backend.health("my-service")
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "docker",
                "compose",
                "ps",
                "--status",
                "running",
                "my-service",
            ]

    def test_health_service_found(self) -> None:
        """Returns True when service_name appears in stdout."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="container-id   my-service   running",
                stderr="",
                returncode=0,
            )
            assert backend.health("my-service") is True

    def test_health_service_not_found(self) -> None:
        """Returns False when service_name is absent from stdout."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stdout="container-id   other-service   running",
                stderr="",
                returncode=0,
            )
            assert backend.health("my-service") is False

    def test_health_does_not_use_check(self) -> None:
        """health() does NOT use check=True (it checks stdout, not exit code)."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            backend.health("svc")
            kwargs = mock_run.call_args[1]
            # check is not passed; defaults to False
            assert kwargs.get("check") is not True

    def test_health_with_project_dir(self) -> None:
        """health() includes --project-directory when set."""
        backend = SubprocessBackend(project_dir="/projects/app")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            backend.health("svc")
            cmd = mock_run.call_args[0][0]
            assert "--project-directory" in cmd


# ===========================================================================
# SubprocessBackend error handling
# ===========================================================================


class TestSubprocessBackendErrors:
    """Verify that subprocess errors propagate correctly."""

    def test_start_raises_on_called_process_error(self) -> None:
        """start() with check=True raises CalledProcessError on failure."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="docker compose up -d svc"
            )
            with pytest.raises(subprocess.CalledProcessError):
                backend.start("svc")

    def test_stop_raises_on_called_process_error(self) -> None:
        """stop() with check=True raises CalledProcessError on failure."""
        backend = SubprocessBackend()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="docker compose stop svc"
            )
            with pytest.raises(subprocess.CalledProcessError):
                backend.stop("svc")


# ===========================================================================
# MockBackend
# ===========================================================================


class TestMockBackendStartStop:
    """Tests for MockBackend start/stop call recording."""

    def test_default_has_empty_call_lists(self) -> None:
        """Fresh MockBackend has empty start_calls and stop_calls."""
        backend = MockBackend()
        assert backend.start_calls == []
        assert backend.stop_calls == []

    def test_start_records_single_call(self) -> None:
        """start() appends (service_name, version) to start_calls."""
        backend = MockBackend()
        backend.start("alpha", version="1.0")
        assert backend.start_calls == [("alpha", "1.0")]

    def test_start_without_version_records_none(self) -> None:
        """start() without version records None as the version."""
        backend = MockBackend()
        backend.start("alpha")
        assert backend.start_calls == [("alpha", None)]

    def test_stop_records_single_call(self) -> None:
        """stop() appends service_name to stop_calls."""
        backend = MockBackend()
        backend.stop("alpha")
        assert backend.stop_calls == ["alpha"]

    def test_multiple_start_calls_accumulate(self) -> None:
        """Multiple start() calls are all recorded in order."""
        backend = MockBackend()
        backend.start("a", version="1")
        backend.start("b", version="2")
        backend.start("c")
        assert backend.start_calls == [
            ("a", "1"),
            ("b", "2"),
            ("c", None),
        ]

    def test_multiple_stop_calls_accumulate(self) -> None:
        """Multiple stop() calls are all recorded in order."""
        backend = MockBackend()
        backend.stop("a")
        backend.stop("b")
        backend.stop("c")
        assert backend.stop_calls == ["a", "b", "c"]


class TestMockBackendHealth:
    """Tests for MockBackend.health() canned results."""

    def test_empty_results_returns_false(self) -> None:
        """Default (empty list) always returns False."""
        backend = MockBackend()
        assert backend.health("svc") is False
        assert backend.health("svc") is False
        assert backend.health("svc") is False

    def test_single_true_always_healthy(self) -> None:
        """A single True repeats forever."""
        backend = MockBackend(health_results=[True])
        for _ in range(5):
            assert backend.health("svc") is True

    def test_single_false_always_unhealthy(self) -> None:
        """A single False repeats forever."""
        backend = MockBackend(health_results=[False])
        for _ in range(5):
            assert backend.health("svc") is False

    def test_iterates_through_results(self) -> None:
        """Returns each result in order, then repeats the last."""
        backend = MockBackend(health_results=[True, False, True])
        assert backend.health("svc") is True
        assert backend.health("svc") is False
        assert backend.health("svc") is True
        # Exhausted — repeats last value
        assert backend.health("svc") is True
        assert backend.health("svc") is True

    def test_two_values_exhausts_to_last(self) -> None:
        """[True, False] → True, False, then False forever."""
        backend = MockBackend(health_results=[True, False])
        assert backend.health("svc") is True
        assert backend.health("svc") is False
        assert backend.health("svc") is False
        assert backend.health("svc") is False

    def test_service_name_is_ignored_by_mock(self) -> None:
        """MockBackend.health() ignores the service_name argument."""
        backend = MockBackend(health_results=[True])
        assert backend.health("anything") is True
        assert backend.health("something-else") is True

    def test_none_constructor_defaults_to_empty(self) -> None:
        """Passing None to health_results is the same as omitting it."""
        backend = MockBackend(health_results=None)
        assert backend.health("svc") is False
