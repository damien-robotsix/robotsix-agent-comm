"""Tests for the exponential-backoff retry helper."""

from __future__ import annotations

import pytest

from robotsix_agent_comm.transport import DeliveryError, RetryPolicy
from robotsix_agent_comm.transport.errors import TransportError
from robotsix_agent_comm.transport.retry import retry_call


def _policy() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=4, base_delay=0.5, max_delay=10.0, backoff_factor=2.0
    )


def test_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransportError("transient")
        return "ok"

    result = retry_call(flaky, _policy(), sleep=sleeps.append)

    assert result == "ok"
    assert calls["n"] == 3
    # Two retries -> two backoff sleeps: 0.5, 1.0.
    assert sleeps == [0.5, 1.0]


def test_backoff_caps_at_max_delay() -> None:
    sleeps: list[float] = []

    def always_fails() -> str:
        raise TransportError("nope")

    policy = RetryPolicy(
        max_attempts=4, base_delay=0.5, max_delay=1.0, backoff_factor=2.0
    )
    with pytest.raises(DeliveryError):
        retry_call(always_fails, policy, sleep=sleeps.append)

    # 0.5, 1.0, then capped at max_delay 1.0; no sleep after the last attempt.
    assert sleeps == [0.5, 1.0, 1.0]


def test_exhausted_retries_raise_delivery_error() -> None:
    cause = TransportError("boom")
    attempts = {"n": 0}

    def always_fails() -> str:
        attempts["n"] += 1
        raise cause

    with pytest.raises(DeliveryError) as excinfo:
        retry_call(always_fails, _policy(), sleep=lambda _delay: None)

    assert attempts["n"] == 4
    assert excinfo.value.cause is cause
    assert excinfo.value.__cause__ is cause
