"""Tests for config-contract base types."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.protocol import (
    REDACTED_SENTINEL,
    ConfigContractError,
    SecretRedactor,
    SettableKey,
)


class TestConfigContractError:
    def test_constructor_stores_code_message_details(self) -> None:
        err = ConfigContractError("INVALID_KEY", "unknown key", key="foo.bar")
        assert err.code == "INVALID_KEY"
        assert err.message == "unknown key"
        assert err.details == {"key": "foo.bar"}

    def test_constructor_without_details(self) -> None:
        err = ConfigContractError("INTERNAL", "something went wrong")
        assert err.code == "INTERNAL"
        assert err.message == "something went wrong"
        assert err.details == {}

    def test_string_representation(self) -> None:
        err = ConfigContractError("BAD_TYPE", "expected int, got str")
        assert str(err) == "[BAD_TYPE] expected int, got str"

    def test_is_exception(self) -> None:
        err = ConfigContractError("X", "msg")
        assert isinstance(err, Exception)

    def test_details_are_independent_kwargs(self) -> None:
        err = ConfigContractError(
            "VALIDATION_FAILED",
            "multiple errors",
            field_a="bad",
            field_b=42,
            nested={"x": 1},
        )
        assert err.details == {
            "field_a": "bad",
            "field_b": 42,
            "nested": {"x": 1},
        }


class TestSecretRedactor:
    def test_is_secret_matches_substring(self) -> None:
        patterns = frozenset({"password", "token", "secret"})
        assert SecretRedactor.is_secret("db.password", patterns) is True
        assert SecretRedactor.is_secret("api_token", patterns) is True
        assert SecretRedactor.is_secret("my_secret_key", patterns) is True
        assert SecretRedactor.is_secret("host", patterns) is False
        assert SecretRedactor.is_secret("port", patterns) is False

    def test_is_secret_empty_patterns(self) -> None:
        assert SecretRedactor.is_secret("db.password", frozenset()) is False

    def test_redact_value_redacts_secrets(self) -> None:
        patterns = frozenset({"password", "key"})
        assert (
            SecretRedactor.redact_value("db.password", "s3cr3t", patterns)
            == REDACTED_SENTINEL
        )
        assert (
            SecretRedactor.redact_value("api_key", "abc123", patterns)
            == REDACTED_SENTINEL
        )

    def test_redact_value_passes_non_secrets(self) -> None:
        patterns = frozenset({"password"})
        assert SecretRedactor.redact_value("host", "localhost", patterns) == "localhost"
        assert SecretRedactor.redact_value("port", 8080, patterns) == 8080
        assert SecretRedactor.redact_value("enabled", True, patterns) is True
        assert SecretRedactor.redact_value("none_val", None, patterns) is None

    def test_REDACTED_SENTINEL_class_attribute(self) -> None:
        assert SecretRedactor.REDACTED_SENTINEL == "***"
        assert SecretRedactor.REDACTED_SENTINEL is REDACTED_SENTINEL


class TestSettableKey:
    def test_fields(self) -> None:
        sk = SettableKey(
            key="server.log_level",
            type="string",
            python_type=str,
            path=["server", "log_level"],
        )
        assert sk.key == "server.log_level"
        assert sk.type == "string"
        assert sk.python_type is str
        assert sk.path == ["server", "log_level"]

    def test_frozen(self) -> None:
        sk = SettableKey(
            key="interval",
            type="integer",
            python_type=int,
            path=["interval"],
        )
        with pytest.raises(AttributeError):
            sk.key = "other"  # type: ignore[misc]
