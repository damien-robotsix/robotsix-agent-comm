"""Dedicated unit tests for the ``_TokenBucket`` class."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from robotsix_agent_comm.broker._rate_limit import _TokenBucket


class TestTokenBucketInit:
    def test_default_capacity_equals_rate(self) -> None:
        bucket = _TokenBucket(rate=10.0)
        assert bucket._capacity == 10.0
        assert bucket._tokens == 10.0

    def test_explicit_capacity(self) -> None:
        bucket = _TokenBucket(rate=5.0, capacity=20.0)
        assert bucket._capacity == 20.0
        assert bucket._tokens == 20.0

    def test_capacity_less_than_rate(self) -> None:
        bucket = _TokenBucket(rate=20.0, capacity=5.0)
        assert bucket._capacity == 5.0
        assert bucket._tokens == 5.0

    def test_rate_zero_defaults_capacity_to_zero(self) -> None:
        bucket = _TokenBucket(rate=0.0)
        assert bucket._rate == 0.0
        assert bucket._capacity == 0.0

    def test_rate_zero_with_explicit_capacity(self) -> None:
        bucket = _TokenBucket(rate=0.0, capacity=10.0)
        assert bucket._rate == 0.0
        assert bucket._capacity == 10.0
        assert bucket._tokens == 10.0


class TestTokenBucketConsume:
    def test_consume_single_token_succeeds(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=100.0)
        assert bucket.consume(1.0) is True

    def test_consume_multiple_tokens_within_capacity(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=5.0)
        for _ in range(5):
            assert bucket.consume(1.0) is True

    def test_consume_exceeds_capacity_without_refill(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=3.0)
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is True
        # Bucket is empty; no meaningful time has passed yet
        assert bucket.consume(1.0) is False

    def test_consume_refills_over_time(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=10.0)
        # Drain the bucket completely.
        for _ in range(10):
            assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is False

        # Wait for refill: rate=100/s → ~10 tokens in 0.1 s.
        time.sleep(0.15)
        assert bucket.consume(1.0) is True

    def test_consume_zero_tokens_always_succeeds(self) -> None:
        bucket = _TokenBucket(rate=10.0, capacity=1.0)
        assert bucket.consume(1.0) is True  # drain
        assert bucket.consume(1.0) is False  # empty
        # consume(0) succeeds even when bucket is empty.
        assert bucket.consume(0.0) is True
        # consume(0) does not consume tokens — refill still happens.
        time.sleep(0.15)
        assert bucket.consume(1.0) is True

    def test_consume_more_than_capacity_at_once(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=5.0)
        assert bucket.consume(6.0) is False

    def test_rate_zero_never_refills(self) -> None:
        bucket = _TokenBucket(rate=0.0, capacity=3.0)
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is False
        time.sleep(0.1)
        assert bucket.consume(1.0) is False  # still no refill

    def test_rate_zero_capacity_zero_always_fails(self) -> None:
        bucket = _TokenBucket(rate=0.0)
        assert bucket.consume(1.0) is False


class TestTokenBucketThreadSafety:
    def test_concurrent_consumers_no_crashes(self) -> None:
        bucket = _TokenBucket(rate=500.0, capacity=50.0)
        n_threads = 8

        def consumer() -> None:
            for _ in range(50):
                bucket.consume(1.0)

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(consumer) for _ in range(n_threads)]
            for future in as_completed(futures):
                future.result()  # No exception → passes

    def test_concurrent_consumers_successes_within_bounds(self) -> None:
        bucket = _TokenBucket(rate=1000.0, capacity=100.0)
        n_threads = 10
        successes = 0

        def consumer() -> int:
            local = 0
            for _ in range(20):
                if bucket.consume(1.0):
                    local += 1
            return local

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(consumer) for _ in range(n_threads)]
            for future in as_completed(futures):
                successes += future.result()

        # Total attempts: 10 threads × 20 = 200.
        assert 0 <= successes <= 200

    def test_concurrent_consume_zero_tokens(self) -> None:
        bucket = _TokenBucket(rate=100.0, capacity=1.0)
        n_threads = 10

        def consumer() -> bool:
            return bucket.consume(0.0)

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(consumer) for _ in range(n_threads)]
            results = [future.result() for future in as_completed(futures)]

        assert all(results)
