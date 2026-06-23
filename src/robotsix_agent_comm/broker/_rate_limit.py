"""Thread-safe token bucket rate limiter."""

from __future__ import annotations

import threading
import time


class _TokenBucket:
    """Thread-safe token bucket rate limiter for a single principal."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self._rate = rate
        self._capacity = capacity if capacity is not None else rate
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume *tokens*.  Returns ``True`` if allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False
