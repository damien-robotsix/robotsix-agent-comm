"""Tests for :class:`LifecycleConfig` parsing and validation."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from robotsix_agent_comm.lifecycle.config import LifecycleConfig

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    """Verify default values when no env vars are set."""
    config = LifecycleConfig.from_env({})
    assert config.agent_id == "lifecycle-server"
    assert config.broker_host == "localhost"
    assert config.broker_port == 8443
    assert config.broker_scheme == "https"
    assert config.broker_token is None
    assert config.broker_tls_ca is None
    assert config.langfuse_public_key is None
    assert config.langfuse_secret_key is None
    assert config.langfuse_host is None


# ---------------------------------------------------------------------------
# Full env parsing
# ---------------------------------------------------------------------------


def test_from_env_all_values() -> None:
    """Set all env vars and verify they are parsed correctly."""
    env: dict[str, str] = {
        "ROBOTSIX_LIFECYCLE_AGENT_ID": "my-lifecycle-agent",
        "ROBOTSIX_LIFECYCLE_BROKER_HOST": "broker.example.com",
        "ROBOTSIX_LIFECYCLE_BROKER_PORT": "9443",
        "ROBOTSIX_LIFECYCLE_BROKER_SCHEME": "https",
        "ROBOTSIX_LIFECYCLE_BROKER_TOKEN": "secret-token-value",
        "ROBOTSIX_LIFECYCLE_BROKER_TLS_CA": "/path/to/ca.pem",
        "ROBOTSIX_LIFECYCLE_LANGFUSE_PUBLIC_KEY": "pk-langfuse-xxx",
        "ROBOTSIX_LIFECYCLE_LANGFUSE_SECRET_KEY": "sk-langfuse-xxx",
        "ROBOTSIX_LIFECYCLE_LANGFUSE_HOST": "https://langfuse.example.com",
    }

    config = LifecycleConfig.from_env(env)

    assert config.agent_id == "my-lifecycle-agent"
    assert config.broker_host == "broker.example.com"
    assert config.broker_port == 9443
    assert config.broker_scheme == "https"
    assert config.broker_token == "secret-token-value"
    assert config.broker_tls_ca == "/path/to/ca.pem"
    assert config.langfuse_public_key == "pk-langfuse-xxx"
    assert config.langfuse_secret_key == "sk-langfuse-xxx"
    assert config.langfuse_host == "https://langfuse.example.com"


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_broker_token_unset_warns(caplog: Any) -> None:
    """When ROBOTSIX_LIFECYCLE_BROKER_TOKEN is not set, validate() emits a warning."""
    caplog.set_level(logging.WARNING)

    LifecycleConfig.from_env({})

    assert "ROBOTSIX_LIFECYCLE_BROKER_TOKEN is not set" in caplog.text


# ---------------------------------------------------------------------------
# Frozen / immutability
# ---------------------------------------------------------------------------


def test_frozen() -> None:
    """Config instances are immutable (hashable, cannot set attributes)."""
    config = LifecycleConfig.from_env({"ROBOTSIX_LIFECYCLE_BROKER_TOKEN": "some-token"})

    # Must be hashable (frozen dataclass).
    _ = hash(config)

    # Attribute assignment must raise FrozenInstanceError.
    with pytest.raises(FrozenInstanceError):
        config.broker_port = 1234  # type: ignore[misc]
