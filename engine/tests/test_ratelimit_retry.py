import random

import pytest

from nexus_engine.marketdata.ratelimit import TokenBucket
from nexus_engine.marketdata.retry import MAX_DELAY_S, backoff_delay, parse_retry_after


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_bucket_allows_burst_then_waits_at_the_configured_rate() -> None:
    c = FakeClock()
    b = TokenBucket(rate_per_s=2, capacity=4, clock=c.now, sleep=c.sleep)
    for _ in range(4):
        assert b.acquire() == 0
    assert b.acquire() == pytest.approx(0.5)
    assert b.acquire(2) == pytest.approx(1.0)


def test_penalty_honors_retry_after() -> None:
    c = FakeClock()
    b = TokenBucket(rate_per_s=1, capacity=1, clock=c.now, sleep=c.sleep)
    b.penalize(5)
    assert b.acquire() == pytest.approx(6.0)


def test_cost_above_capacity_is_an_error() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_s=1, capacity=1).acquire(2)


def test_backoff_grows_is_capped_and_respects_retry_after() -> None:
    rng = random.Random(1)
    delays = [backoff_delay(a, rng=rng) for a in range(1, 10)]
    assert all(0.5 <= d for d in delays) and max(delays) <= MAX_DELAY_S
    assert backoff_delay(1, retry_after_s=30, rng=rng) >= 30
    assert parse_retry_after("7") == 7 and parse_retry_after("soon") is None and parse_retry_after("-1") is None
