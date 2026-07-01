"""Env-var configuration parser and validator for the lifecycle server.

Produces a frozen :class:`LifecycleConfig` from ``ROBOTSIX_LIFECYCLE_*``
environment variables (or an explicit ``Mapping``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from ..protocol._config_helpers import make_env_getter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LifecycleConfig:
    """Immutable configuration for the lifecycle server.

    Construct via :meth:`from_env` (preferred) or directly with keyword
    arguments (for tests).
    """

    agent_id: str = "lifecycle-server"
    broker_host: str = "localhost"
    broker_port: int = 8443
    broker_scheme: str = "https"
    broker_token: str | None = None
    broker_tls_ca: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

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
                ``None`` to read the real process environment.

        Returns:
            A populated-and-validated :class:`LifecycleConfig`.
        """
        _get = make_env_getter(env)

        broker_token = _get("ROBOTSIX_LIFECYCLE_BROKER_TOKEN") or None
        broker_tls_ca = _get("ROBOTSIX_LIFECYCLE_BROKER_TLS_CA") or None
        langfuse_public_key = _get("ROBOTSIX_LIFECYCLE_LANGFUSE_PUBLIC_KEY") or None
        langfuse_secret_key = _get("ROBOTSIX_LIFECYCLE_LANGFUSE_SECRET_KEY") or None
        langfuse_host = _get("ROBOTSIX_LIFECYCLE_LANGFUSE_HOST") or None

        config = cls(
            agent_id=_get("ROBOTSIX_LIFECYCLE_AGENT_ID", "lifecycle-server"),
            broker_host=_get("ROBOTSIX_LIFECYCLE_BROKER_HOST", "localhost"),
            broker_port=int(_get("ROBOTSIX_LIFECYCLE_BROKER_PORT", "8443")),
            broker_scheme=_get("ROBOTSIX_LIFECYCLE_BROKER_SCHEME", "https"),
            broker_token=broker_token,
            broker_tls_ca=broker_tls_ca,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            langfuse_host=langfuse_host,
        )

        config.validate()
        return config

    # -- Validation ------------------------------------------------------

    def validate(self) -> None:
        """Validate this config, emitting warnings for non-critical issues.

        A missing ``broker_token`` triggers a warning since the broker may
        have authentication disabled in development, but it should never
        be missing in production.
        """
        if not self.broker_token:
            logger.warning(
                "ROBOTSIX_LIFECYCLE_BROKER_TOKEN is not set — "
                "the lifecycle server will connect to the broker "
                "without a bearer token.  This is acceptable when "
                "broker authentication is disabled, but should "
                "never be the case in production."
            )
