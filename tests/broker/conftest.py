"""Shared fixtures for broker tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def cert_and_token_files(tmp_path: Any) -> dict[str, Any]:
    """Create dummy cert/key/token files and return env overrides and paths.

    Returns a dict with:
      - ``ROBOTSIX_BROKER_TLS_CERT``, ``ROBOTSIX_BROKER_TLS_KEY``,
        ``ROBOTSIX_BROKER_AGENT_TOKENS_FILE`` — string paths for the env dict.
      - ``cert``, ``key``, ``token_file`` — :class:`pathlib.Path` objects
        so callers can write test-specific content (e.g. ``token_file.write_text(…)``).
    """
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    token_file = tmp_path / "tokens.json"
    for f in (cert, key):
        f.write_text("dummy")
    return {
        "ROBOTSIX_BROKER_TLS_CERT": str(cert),
        "ROBOTSIX_BROKER_TLS_KEY": str(key),
        "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
        "cert": cert,
        "key": key,
        "token_file": token_file,
    }


@pytest.fixture
def tls_auth_broker(tmp_path: Any) -> tuple[Any, str]:
    """Build and start a TLS+auth BrokerServer; stop on teardown.

    Yields ``(broker, ca_path)`` so tests can use the broker instance
    and build a client TLS context that trusts the CA.
    """
    from robotsix_agent_comm.broker import BrokerConfig, build_broker
    from tests.helpers import _write_certs_to_dir

    ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))
    config = BrokerConfig(
        host="127.0.0.1",
        port=0,
        tls_cert=cert_path,
        tls_key=key_path,
        agent_tokens={"agent-a": "tok-a"},
    )
    broker = build_broker(config)
    broker.start()
    yield broker, ca_path
    broker.stop()
