"""Tests for :class:`BrokerConfig` parsing and validation."""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from robotsix_agent_comm.broker.config import BrokerConfig, _parse_inline_tokens

# ---------------------------------------------------------------------------
# Inline token parsing
# ---------------------------------------------------------------------------


class TestParseInlineTokens:
    def test_single_pair(self) -> None:
        result = _parse_inline_tokens("agent-a=tok-a")
        assert result == {"agent-a": "tok-a"}

    def test_multiple_pairs(self) -> None:
        result = _parse_inline_tokens("agent-a=tok-a,agent-b=tok-b")
        assert result == {"agent-a": "tok-a", "agent-b": "tok-b"}

    def test_whitespace_around_equals(self) -> None:
        result = _parse_inline_tokens("a = v1 , b = v2")
        assert result == {"a": "v1", "b": "v2"}

    def test_trailing_comma_ignored(self) -> None:
        result = _parse_inline_tokens("a=v1,")
        assert result == {"a": "v1"}

    def test_empty_string_yields_empty_dict(self) -> None:
        assert _parse_inline_tokens("") == {}

    def test_missing_equals_raises(self) -> None:
        with pytest.raises(ValueError, match="expected id=token format"):
            _parse_inline_tokens("bad-entry")

    def test_empty_agent_id_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_id is empty"):
            _parse_inline_tokens("=token")

    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="token is empty"):
            _parse_inline_tokens("agent-a=")

    def test_token_contains_equals(self) -> None:
        """Token value may contain '=' characters."""
        result = _parse_inline_tokens("a=b=c")
        assert result == {"a": "b=c"}


# ---------------------------------------------------------------------------
# from_env basics
# ---------------------------------------------------------------------------


class TestFromEnvBasics:
    def test_defaults_when_empty_env(self) -> None:
        # Use development mode to bypass production TLS/auth requirements.
        config = BrokerConfig.from_env({"ROBOTSIX_BROKER_ENV": "development"})
        assert config.host == "0.0.0.0"
        assert config.port == 8443
        assert config.env == "development"
        assert config.tls_cert is None
        assert config.tls_key is None
        assert config.tls_ca is None
        assert config.require_client_cert is False
        assert config.agent_tokens is None
        assert config.ttl_seconds is None
        assert config.rate_limit is None
        assert config.max_body_size is None
        assert config.audit_log is None

    def test_parses_each_var(self, tmp_path: Any) -> None:
        # Write a token file.
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({"a": "ta", "b": "tb"}))

        # Create a dummy cert/key so they pass file-existence checks.
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        ca_file = tmp_path / "ca.pem"
        for f in (cert_file, key_file, ca_file):
            f.write_text("dummy")

        env: dict[str, str] = {
            "ROBOTSIX_BROKER_HOST": "127.0.0.1",
            "ROBOTSIX_BROKER_PORT": "9443",
            "ROBOTSIX_BROKER_ENV": "production",  # prod so TLS required
            "ROBOTSIX_BROKER_TLS_CERT": str(cert_file),
            "ROBOTSIX_BROKER_TLS_KEY": str(key_file),
            "ROBOTSIX_BROKER_TLS_CA": str(ca_file),
            "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": "true",
            "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            "ROBOTSIX_BROKER_TTL_SECONDS": "120",
            "ROBOTSIX_BROKER_RATE_LIMIT": "5.5",
            "ROBOTSIX_BROKER_MAX_BODY_SIZE": "512000",
            "ROBOTSIX_BROKER_AUDIT_LOG": "/tmp/audit.log",
        }

        config = BrokerConfig.from_env(env)
        assert config.host == "127.0.0.1"
        assert config.port == 9443
        assert config.env == "production"
        assert config.tls_cert == str(cert_file)
        assert config.tls_key == str(key_file)
        assert config.tls_ca == str(ca_file)
        assert config.require_client_cert is True
        assert config.agent_tokens == {"a": "ta", "b": "tb"}
        assert config.ttl_seconds == 120
        assert config.rate_limit == 5.5
        assert config.max_body_size == 512000
        assert config.audit_log == "/tmp/audit.log"


# ---------------------------------------------------------------------------
# Agent token precedence: file wins over inline
# ---------------------------------------------------------------------------


class TestTokenPrecedence:
    def test_file_wins_over_inline(self, tmp_path: Any) -> None:
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({"file-agent": "file-tok"}))

        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_text("dummy")
        key_file.write_text("dummy")

        env: dict[str, str] = {
            "ROBOTSIX_BROKER_ENV": "production",
            "ROBOTSIX_BROKER_TLS_CERT": str(cert_file),
            "ROBOTSIX_BROKER_TLS_KEY": str(key_file),
            "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            "ROBOTSIX_BROKER_AGENT_TOKENS": "inline-agent=inline-tok",
        }
        config = BrokerConfig.from_env(env)
        assert config.agent_tokens == {"file-agent": "file-tok"}

    def test_inline_used_when_no_file(self, tmp_path: Any) -> None:
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_text("dummy")
        key_file.write_text("dummy")

        env: dict[str, str] = {
            "ROBOTSIX_BROKER_ENV": "production",
            "ROBOTSIX_BROKER_TLS_CERT": str(cert_file),
            "ROBOTSIX_BROKER_TLS_KEY": str(key_file),
            "ROBOTSIX_BROKER_AGENT_TOKENS": "a=ta,b=tb",
        }
        config = BrokerConfig.from_env(env)
        assert config.agent_tokens == {"a": "ta", "b": "tb"}


# ---------------------------------------------------------------------------
# Boolean parsing
# ---------------------------------------------------------------------------


class TestBooleanParsing:
    @pytest.mark.parametrize(
        "val",
        ["1", "true", "yes", "TRUE", "YES", "True", " trUE "],
    )
    def test_truthy(self, val: str, tmp_path: Any) -> None:
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert_file, key_file):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))

        env: dict[str, str] = {
            "ROBOTSIX_BROKER_ENV": "production",
            "ROBOTSIX_BROKER_TLS_CERT": str(cert_file),
            "ROBOTSIX_BROKER_TLS_KEY": str(key_file),
            "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": val,
            "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            "ROBOTSIX_BROKER_TLS_CA": str(cert_file),  # need CA for mTLS
        }
        config = BrokerConfig.from_env(env)
        assert config.require_client_cert is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "", "FALSE", "whatever"])
    def test_falsey(self, val: str) -> None:
        env: dict[str, str] = {
            "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": val,
            "ROBOTSIX_BROKER_ENV": "development",  # dev mode to bypass TLS requirement
        }
        config = BrokerConfig.from_env(env)
        assert config.require_client_cert is False


# ---------------------------------------------------------------------------
# Production validation — TLS
# ---------------------------------------------------------------------------


class TestProductionTLSRequired:
    def test_missing_cert_raises(self) -> None:
        with pytest.raises(ValueError, match="TLS is required in production"):
            BrokerConfig.from_env(
                {"ROBOTSIX_BROKER_ENV": "production", "ROBOTSIX_BROKER_TLS_KEY": "/k"}
            )

    def test_missing_key_raises(self) -> None:
        with pytest.raises(ValueError, match="TLS is required in production"):
            BrokerConfig.from_env(
                {"ROBOTSIX_BROKER_ENV": "production", "ROBOTSIX_BROKER_TLS_CERT": "/c"}
            )

    def test_cert_file_nonexistent_raises(self) -> None:
        with pytest.raises(ValueError, match="TLS certificate file not found"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": "/nonexistent/cert.pem",
                    "ROBOTSIX_BROKER_TLS_KEY": "/nonexistent/key.pem",
                }
            )

    def test_key_file_nonexistent_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy")
        with pytest.raises(ValueError, match="TLS key file not found"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": "/nonexistent/key.pem",
                }
            )


# ---------------------------------------------------------------------------
# Production validation — auth
# ---------------------------------------------------------------------------


class TestProductionAuthRequired:
    def test_no_tokens_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("dummy")
        key.write_text("dummy")
        with pytest.raises(
            ValueError, match="authentication is required in production"
        ):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                }
            )

    def test_empty_tokens_file_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text("{}")
        with pytest.raises(ValueError, match="agent tokens mapping is empty"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )

    def test_valid_tokens_succeeds(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))
        config = BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "production",
                "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                "ROBOTSIX_BROKER_TLS_KEY": str(key),
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            }
        )
        assert config.agent_tokens == {"a": "t"}


# ---------------------------------------------------------------------------
# Production validation — mTLS
# ---------------------------------------------------------------------------


class TestProductionMTLS:
    def test_require_client_cert_without_ca_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))
        with pytest.raises(ValueError, match="mutual TLS requires a CA"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": "true",
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )

    def test_require_client_cert_with_nonexistent_ca_raises(
        self, tmp_path: Any
    ) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))
        with pytest.raises(ValueError, match="TLS CA file not found"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_TLS_CA": "/nonexistent/ca.pem",
                    "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": "true",
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )

    def test_require_client_cert_with_valid_ca_succeeds(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        ca = tmp_path / "ca.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key, ca):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))
        config = BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "production",
                "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                "ROBOTSIX_BROKER_TLS_KEY": str(key),
                "ROBOTSIX_BROKER_TLS_CA": str(ca),
                "ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT": "true",
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            }
        )
        assert config.require_client_cert is True


# ---------------------------------------------------------------------------
# Development mode warnings
# ---------------------------------------------------------------------------


class TestDevModeWarnings:
    def test_no_tls_dev_mode_warning_in_log(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING)
        BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "development",
                "ROBOTSIX_BROKER_AGENT_TOKENS": "a=t",
            }
        )
        assert "DEVELOPMENT MODE" in caplog.text
        assert "TLS is not configured" in caplog.text

    def test_no_auth_dev_mode_warning_in_log(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING)
        BrokerConfig.from_env({"ROBOTSIX_BROKER_ENV": "development"})
        assert "DEVELOPMENT MODE" in caplog.text
        assert "authentication is not configured" in caplog.text

    def test_empty_tokens_dev_mode_warning_in_log(
        self, caplog: Any, tmp_path: Any
    ) -> None:
        caplog.set_level(logging.WARNING)
        token_file = tmp_path / "tokens.json"
        token_file.write_text("{}")
        BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "development",
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            }
        )
        # The file contains an empty object, triggering
        # the empty-mapping warning.
        assert "agent tokens mapping is empty" in caplog.text

    def test_dev_mode_with_tls_and_auth_no_warnings(
        self, caplog: Any, tmp_path: Any
    ) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))

        caplog.set_level(logging.WARNING)
        BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "development",
                "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                "ROBOTSIX_BROKER_TLS_KEY": str(key),
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            }
        )
        assert "DEVELOPMENT MODE" not in caplog.text


# ---------------------------------------------------------------------------
# Token file validation
# ---------------------------------------------------------------------------


class TestTokenFileValidation:
    def test_nonexistent_file_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        for f in (cert, key):
            f.write_text("dummy")
        with pytest.raises(ValueError, match="cannot read file"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": "/nonexistent/tokens.json",
                }
            )

    def test_invalid_json_in_file_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text("not json")
        with pytest.raises(ValueError, match="invalid JSON"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )

    def test_array_in_file_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text("[1,2,3]")
        with pytest.raises(ValueError, match="must be a JSON object"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )

    def test_non_string_token_value_raises(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": 123}))
        with pytest.raises(ValueError, match="must be a string"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                    "ROBOTSIX_BROKER_TLS_KEY": str(key),
                    "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                }
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_tls_cert_set_but_empty_string_treated_as_unset(self) -> None:
        """Empty string should be treated as not set."""
        with pytest.raises(ValueError, match="TLS is required in production"):
            BrokerConfig.from_env(
                {
                    "ROBOTSIX_BROKER_ENV": "production",
                    "ROBOTSIX_BROKER_TLS_CERT": "",
                    "ROBOTSIX_BROKER_TLS_KEY": "/k",
                }
            )

    def test_unknown_env_var_ignored(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))

        config = BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "production",
                "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                "ROBOTSIX_BROKER_TLS_KEY": str(key),
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
                "UNRELATED_VAR": "ignored",
            }
        )
        assert config.host == "0.0.0.0"  # default

    def test_config_is_frozen(self, tmp_path: Any) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        token_file = tmp_path / "tokens.json"
        for f in (cert, key):
            f.write_text("dummy")
        token_file.write_text(json.dumps({"a": "t"}))

        config = BrokerConfig.from_env(
            {
                "ROBOTSIX_BROKER_ENV": "production",
                "ROBOTSIX_BROKER_TLS_CERT": str(cert),
                "ROBOTSIX_BROKER_TLS_KEY": str(key),
                "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
            }
        )
        with pytest.raises(FrozenInstanceError):
            config.port = 1234  # type: ignore[misc]
