"""Env-var configuration parser and validator for the lifecycle server.

Produces a frozen :class:`LifecycleConfig` from
``ROBOTSIX_LIFECYCLE_*`` environment variables (or an explicit
``Mapping``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_bool(raw: str) -> bool:
    """Accept ``1`` / ``true`` / ``yes`` (case-insensitive) as truthy."""
    return raw.strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# LifecycleConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleConfig:
    """Immutable configuration for a :class:`LifecycleServer`.

    Construct via :meth:`from_env` (preferred) or directly with keyword
    arguments (for tests).
    """

    host: str = "0.0.0.0"  # nosec B104 -- server must accept external connections
    port: int = 8500
    env: str = "production"
    auth_token: str | None = None
    health_timeout_seconds: float = 30.0
    health_interval_seconds: float = 2.0
    health_check_enabled: bool = True

    # -- Factory ---------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> LifecycleConfig:
        """Parse configuration from *env* (defaults to ``os.environ``).

        Parameters:
            env:
                An explicit ``Mapping`` of variable names to values, or
                ``None`` to read the real process environment.  Passing a
                plain ``dict`` keeps tests isolated.

        Returns:
            A populated-and-validated :class:`LifecycleConfig`.
        """
        if env is None:
            env = os.environ

        def _get(key: str, default: str = "") -> str:
            return env.get(key, default)

        raw_health_check = _get("ROBOTSIX_LIFECYCLE_HEALTH_CHECK_ENABLED")

        config = cls(
            host=_get("ROBOTSIX_LIFECYCLE_HOST", "0.0.0.0"),  # nosec B104
            port=int(_get("ROBOTSIX_LIFECYCLE_PORT", "8500")),
            env=_get("ROBOTSIX_LIFECYCLE_ENV", "production"),
            auth_token=_get("ROBOTSIX_LIFECYCLE_AUTH_TOKEN") or None,
            health_timeout_seconds=float(
                _get("ROBOTSIX_LIFECYCLE_HEALTH_TIMEOUT_SECONDS", "30.0")
            ),
            health_interval_seconds=float(
                _get("ROBOTSIX_LIFECYCLE_HEALTH_INTERVAL_SECONDS", "2.0")
            ),
            health_check_enabled=_parse_bool(raw_health_check)
            if raw_health_check
            else True,
        )

        config.validate()
        return config

    # -- Validation ------------------------------------------------------

    def validate(self) -> None:
        """Validate this config, raising :exc:`ValueError` on failure."""
        is_prod = self.env != "development"

        if is_prod and not self.auth_token:
            raise ValueError(
                "authentication is required in production: "
                "set ROBOTSIX_LIFECYCLE_AUTH_TOKEN"
            )

        if self.health_timeout_seconds <= 0:
            raise ValueError("health_timeout_seconds must be positive")

        if self.health_interval_seconds <= 0:
            raise ValueError("health_interval_seconds must be positive")

        if not is_prod and not self.auth_token:
            logger.warning(
                "DEVELOPMENT MODE: authentication is not configured — "
                "the lifecycle server will accept anonymous requests. "
                "Set ROBOTSIX_LIFECYCLE_AUTH_TOKEN to enable authentication."
            )
