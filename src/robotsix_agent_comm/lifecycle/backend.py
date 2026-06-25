"""Pluggable lifecycle execution backends.

Provides :class:`LifecycleBackend` (abstract interface) and concrete
implementations for Docker Compose (subprocess) and testing (mock).
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod


class LifecycleBackend(ABC):
    """Abstract interface for service lifecycle operations.

    Implementations encapsulate how to start, stop, and health-check
    a managed service (e.g. via Docker Compose, Kubernetes, systemd).
    """

    @abstractmethod
    def start(self, service_name: str, version: str | None = None) -> None:
        """Bring *service_name* online, optionally at a specific *version*.

        Args:
            service_name: The service identifier (e.g. Compose service name).
            version: An optional image tag / version string.  The backend
                must arrange for this version to be deployed (e.g. by
                setting an environment variable read by the Compose file).

        Raises:
            subprocess.CalledProcessError: When the underlying command fails.
        """
        ...

    @abstractmethod
    def stop(self, service_name: str) -> None:
        """Stop *service_name*.

        Raises:
            subprocess.CalledProcessError: When the underlying command fails.
        """
        ...

    @abstractmethod
    def health(self, service_name: str) -> bool:
        """Return ``True`` when *service_name* is healthy/running.

        Returns:
            ``True`` if the service is reachable and healthy.
        """
        ...


class SubprocessBackend(LifecycleBackend):
    """Docker Compose backend that shells out to ``docker compose``.

    Parameters:
        project_dir:
            Directory containing ``docker-compose.yml``.  Defaults to
            the current working directory.
    """

    def __init__(self, project_dir: str | None = None) -> None:
        """Create a SubprocessBackend.

        Args:
            project_dir: Directory containing ``docker-compose.yml``.
                Defaults to the current working directory.
        """
        self._project_dir = project_dir

    def _compose_cmd(self) -> list[str]:
        cmd = ["docker", "compose"]
        if self._project_dir is not None:
            cmd.extend(["--project-directory", self._project_dir])
        return cmd

    def start(self, service_name: str, version: str | None = None) -> None:
        """Run ``docker compose up -d <service_name>``.

        When *version* is supplied the environment variable
        ``SERVICE_VERSION`` is set so the Compose file can reference it
        (e.g. ``image: my-svc:${SERVICE_VERSION:-latest}``).
        """
        env = os.environ.copy()
        if version:
            env["SERVICE_VERSION"] = version
        cmd = [*self._compose_cmd(), "up", "-d", service_name]
        subprocess.run(  # noqa: S603
            cmd, env=env, check=True, capture_output=True, text=True
        )

    def stop(self, service_name: str) -> None:
        """Run ``docker compose stop <service_name>``."""
        cmd = [*self._compose_cmd(), "stop", service_name]
        subprocess.run(  # noqa: S603
            cmd, check=True, capture_output=True, text=True
        )

    def health(self, service_name: str) -> bool:
        """Check whether *service_name* has a running container.

        Uses ``docker compose ps --status running`` and looks for
        *service_name* in the output.
        """
        cmd = [*self._compose_cmd(), "ps", "--status", "running", service_name]
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True
        )
        return service_name in result.stdout


class MockBackend(LifecycleBackend):
    """Test double that records calls and returns canned results.

    Parameters:
        health_results:
            A list of booleans returned by successive :meth:`health` calls.
            Once exhausted, ``health()`` returns ``False`` by default.
    """

    def __init__(self, health_results: list[bool] | None = None) -> None:
        """Create a MockBackend.

        Args:
            health_results: Optional list of booleans returned by successive
                :meth:`health` calls.  When the list is exhausted the *last*
                value is repeated, so ``[False]`` means "always unhealthy"
                and ``[True]`` means "always healthy".  Defaults to an empty
                list (always returns ``False``).
        """
        self._health_results = health_results or []
        self._health_calls = 0
        self.start_calls: list[tuple[str, str | None]] = []
        self.stop_calls: list[str] = []

    def start(self, service_name: str, version: str | None = None) -> None:
        """Record the call details."""
        self.start_calls.append((service_name, version))

    def stop(self, service_name: str) -> None:
        """Record the call details."""
        self.stop_calls.append(service_name)

    def health(self, service_name: str) -> bool:
        """Return the next canned result.

        When the result list is exhausted the *last* value is repeated
        so that a single ``[False]`` means "always unhealthy" and a
        single ``[True]`` means "always healthy".
        """
        if not self._health_results:
            return False
        if self._health_calls < len(self._health_results):
            result = self._health_results[self._health_calls]
        else:
            result = self._health_results[-1]
        self._health_calls += 1
        return result
