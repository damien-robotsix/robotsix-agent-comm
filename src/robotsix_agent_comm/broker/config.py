"""Env-var configuration parser and validator for the agent-comm broker.

Produces a frozen :class:`BrokerConfig` from ``ROBOTSIX_BROKER_*``
environment variables (or an explicit ``Mapping``).  Validation enforces
the security model from ADR 0006: production mode requires TLS and
per-agent authentication; development mode relaxes those checks but
emits loud warnings.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_bool(raw: str) -> bool:
    """Accept ``1`` / ``true`` / ``yes`` (case-insensitive) as truthy."""
    return raw.strip().lower() in ("1", "true", "yes")


def _parse_inline_tokens(raw: str) -> dict[str, str]:
    """Parse ``id1=token1,id2=token2`` into ``{id: token}``.

    Raises :exc:`ValueError` for any malformed entry.
    """
    tokens: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue  # skip empty entries (e.g. trailing comma)
        if "=" not in entry:
            raise ValueError(
                f"malformed token entry {entry!r}: expected id=token format"
            )
        agent_id, _, token = entry.partition("=")
        agent_id = agent_id.strip()
        token = token.strip()
        if not agent_id:
            raise ValueError(f"malformed token entry {entry!r}: agent_id is empty")
        if not token:
            raise ValueError(f"malformed token entry {entry!r}: token is empty")
        tokens[agent_id] = token
    return tokens


# ---------------------------------------------------------------------------
# BrokerConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrokerConfig:
    """Immutable configuration for a :class:`BrokerServer`.

    Construct via :meth:`from_env` (preferred) or directly with keyword
    arguments (for tests).
    """

    host: str = "0.0.0.0"  # nosec B104 -- server must accept external connections
    port: int = 8443
    env: str = "production"

    # TLS
    tls_cert: str | None = None
    tls_key: str | None = None
    tls_ca: str | None = None
    require_client_cert: bool = False

    # Auth
    agent_tokens: dict[str, str] | None = None

    # Tunables
    ttl_seconds: int = 60
    rate_limit: float | None = None
    max_body_size: int | None = None
    mailbox_grace_seconds: float | None = None

    # Audit
    audit_log: str | None = None

    # Dashboard
    dashboard_enabled: bool = False

    # -- Factory ---------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        _read_file: Any = None,
    ) -> BrokerConfig:
        """Parse configuration from *env* (defaults to ``os.environ``).

        Parameters:
            env:
                An explicit ``Mapping`` of variable names to values, or
                ``None`` to read the real process environment.  Passing a
                plain ``dict`` keeps tests isolated.
            _read_file:
                Internal parameter for dependency injection in tests.
                Do not use.

        Returns:
            A populated-but-not-yet-validated :class:`BrokerConfig`.
        """
        if env is None:
            env = os.environ

        def _get(key: str, default: str = "") -> str:
            return env.get(key, default)

        # -- Parse each variable -----------------------------------------
        raw_tls_cert = _get("ROBOTSIX_BROKER_TLS_CERT")
        raw_tls_key = _get("ROBOTSIX_BROKER_TLS_KEY")
        raw_tls_ca = _get("ROBOTSIX_BROKER_TLS_CA")
        raw_require_cc = _get("ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT")
        raw_tokens_file = _get("ROBOTSIX_BROKER_AGENT_TOKENS_FILE")
        raw_tokens_inline = _get("ROBOTSIX_BROKER_AGENT_TOKENS")
        raw_ttl = _get("ROBOTSIX_BROKER_TTL_SECONDS", "60")
        raw_rate = _get("ROBOTSIX_BROKER_RATE_LIMIT")
        raw_body = _get("ROBOTSIX_BROKER_MAX_BODY_SIZE")
        raw_audit = _get("ROBOTSIX_BROKER_AUDIT_LOG")
        raw_mailbox_grace = _get("ROBOTSIX_BROKER_MAILBOX_GRACE_SECONDS")
        raw_dashboard = _get("ROBOTSIX_BROKER_DASHBOARD_ENABLED")

        # -- Auth: resolve tokens (file wins over inline) -----------------
        agent_tokens: dict[str, str] | None = None

        if raw_tokens_file:
            # Read the JSON file.
            path = raw_tokens_file
            try:
                if _read_file is not None:
                    raw_json = _read_file(path)
                else:
                    with open(path, encoding="utf-8") as fh:
                        raw_json = fh.read()
            except OSError as exc:
                raise ValueError(
                    f"ROBOTSIX_BROKER_AGENT_TOKENS_FILE {path!r}: "
                    f"cannot read file: {exc}"
                ) from exc
            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"ROBOTSIX_BROKER_AGENT_TOKENS_FILE {path!r}: invalid JSON: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ValueError(
                    f"ROBOTSIX_BROKER_AGENT_TOKENS_FILE {path!r}: "
                    f"top-level value must be a JSON object, got {type(data).__name__}"
                )
            # Validate every value is a string.
            for k, v in data.items():
                if not isinstance(v, str):
                    raise ValueError(
                        f"ROBOTSIX_BROKER_AGENT_TOKENS_FILE {path!r}: "
                        f"token for {k!r} must be a string, got {type(v).__name__}"
                    )
            agent_tokens = dict(data)
        elif raw_tokens_inline:
            agent_tokens = _parse_inline_tokens(raw_tokens_inline)

        # -- Build config -------------------------------------------------
        config = cls(
            host=_get("ROBOTSIX_BROKER_HOST", "0.0.0.0"),  # nosec B104 -- server must accept external connections
            port=int(_get("ROBOTSIX_BROKER_PORT", "8443")),
            env=_get("ROBOTSIX_BROKER_ENV", "production"),
            tls_cert=raw_tls_cert or None,
            tls_key=raw_tls_key or None,
            tls_ca=raw_tls_ca or None,
            require_client_cert=_parse_bool(raw_require_cc)
            if raw_require_cc
            else False,
            agent_tokens=agent_tokens,
            ttl_seconds=int(raw_ttl),
            rate_limit=float(raw_rate) if raw_rate else None,
            max_body_size=int(raw_body) if raw_body else None,
            audit_log=raw_audit or None,
            mailbox_grace_seconds=(
                float(raw_mailbox_grace) if raw_mailbox_grace else None
            ),
            dashboard_enabled=_parse_bool(raw_dashboard) if raw_dashboard else False,
        )

        config.validate()
        return config

    # -- Validation ------------------------------------------------------

    def validate(self) -> None:
        """Validate this config, raising :exc:`ValueError` on failure.

        Production vs development semantics per ADR 0006 §2:
        - **production:** TLS + auth are mandatory.
        - **development:** relaxations are allowed but each emits a
          :func:`logging.warning` so insecure boots are never silent.
        """
        is_prod = self.env != "development"

        # -- TLS -----------------------------------------------------------------
        if is_prod:
            if not self.tls_cert:
                raise ValueError(
                    "TLS is required in production: "
                    "set ROBOTSIX_BROKER_TLS_CERT and ROBOTSIX_BROKER_TLS_KEY"
                )
            if not self.tls_key:
                raise ValueError(
                    "TLS is required in production: "
                    "set ROBOTSIX_BROKER_TLS_CERT and ROBOTSIX_BROKER_TLS_KEY"
                )
            if not _file_readable(self.tls_cert):
                raise ValueError(
                    f"TLS certificate file not found or not readable: {self.tls_cert!r}"
                )
            if not _file_readable(self.tls_key):
                raise ValueError(
                    f"TLS key file not found or not readable: {self.tls_key!r}"
                )
        else:
            if not self.tls_cert or not self.tls_key:
                logger.warning(
                    "DEVELOPMENT MODE: TLS is not configured — "
                    "the broker will run without transport encryption. "
                    "Set ROBOTSIX_BROKER_TLS_CERT and ROBOTSIX_BROKER_TLS_KEY "
                    "to secure communication."
                )
            elif not (_file_readable(self.tls_cert) and _file_readable(self.tls_key)):
                logger.warning(
                    "DEVELOPMENT MODE: TLS certificate or key file is "
                    "missing or unreadable — TLS will not be applied."
                )

        # -- mTLS CA ------------------------------------------------------------
        if self.require_client_cert:
            if not self.tls_ca:
                raise ValueError(
                    "mutual TLS requires a CA bundle: set ROBOTSIX_BROKER_TLS_CA"
                )
            if not _file_readable(self.tls_ca):
                raise ValueError(
                    f"TLS CA file not found or not readable: {self.tls_ca!r}"
                )

        # -- Auth ---------------------------------------------------------------
        if is_prod:
            if self.agent_tokens is None:
                raise ValueError(
                    "authentication is required in production: "
                    "configure ROBOTSIX_BROKER_AGENT_TOKENS_FILE "
                    "or ROBOTSIX_BROKER_AGENT_TOKENS"
                )
            if not self.agent_tokens:
                raise ValueError(
                    "authentication is required in production: "
                    "agent tokens mapping is empty — "
                    "at least one agent token must be configured"
                )
        else:
            if self.agent_tokens is None:
                logger.warning(
                    "DEVELOPMENT MODE: authentication is not configured — "
                    "the broker will accept anonymous requests. "
                    "Set ROBOTSIX_BROKER_AGENT_TOKENS_FILE or "
                    "ROBOTSIX_BROKER_AGENT_TOKENS to enable authentication."
                )
            elif not self.agent_tokens:
                logger.warning(
                    "DEVELOPMENT MODE: agent tokens mapping is empty — "
                    "the broker will reject all authenticated requests "
                    "(no valid tokens).  Configure at least one token pair."
                )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _file_readable(path: str) -> bool:
    """Return ``True`` when *path* exists and is a readable regular file."""
    try:
        return os.path.isfile(path) and os.access(path, os.R_OK)
    except OSError:
        return False
