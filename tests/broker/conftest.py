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
