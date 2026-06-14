"""Retry policy and exponential-backoff helper.

The baseline transport (ADR 0004) is no-retry, but a network transport
must tolerate transient failures. :class:`RetryPolicy` and
:func:`retry_call` add bounded, exponential-backoff retries *behind the
same transport interface* (ADR 0002) without changing message semantics.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .errors import DeliveryError, TransportError


@dataclass(kw_only=True)
class RetryPolicy:
    """Bounded exponential-backoff configuration.

    The delay before the *n*-th retry is
    ``min(base_delay * backoff_factor ** (attempt - 1), max_delay)``.
    """

    max_attempts: int
    base_delay: float
    max_delay: float
    backoff_factor: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Return the backoff delay (seconds) before retrying ``attempt``.

        ``attempt`` is 1-indexed; the delay following attempt 1 uses
        ``base_delay``.
        """
        return min(
            self.base_delay * self.backoff_factor ** (attempt - 1),
            self.max_delay,
        )


def retry_call[T](
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], object] = time.sleep,
) -> T:
    """Execute ``func`` with retries per ``policy``, backing off between tries.

    ``func`` is retried on :class:`TransportError`. Between attempts the
    injected ``sleep`` is called (default :func:`time.sleep`) so tests can
    run without real delays. When all attempts fail, the last error is
    re-raised wrapped in :class:`DeliveryError`.
    """
    last_exc: TransportError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except TransportError as exc:
            last_exc = exc
            if attempt >= policy.max_attempts:
                break
            sleep(policy.delay_for(attempt))
    raise DeliveryError(
        f"delivery failed after {policy.max_attempts} attempt(s)",
        cause=last_exc,
    ) from last_exc
