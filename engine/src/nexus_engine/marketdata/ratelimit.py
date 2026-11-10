"""Token-bucket rate limiter with an injectable clock (deterministic tests)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_s: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_s <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._rate = rate_per_s
        self._capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    def acquire(self, cost: float = 1.0) -> float:
        """Block until `cost` tokens are available. Returns the total time waited."""
        if cost > self._capacity:
            raise ValueError("cost exceeds bucket capacity")
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return waited
                wait = (cost - self._tokens) / self._rate
            self._sleep(wait)
            waited += wait

    def penalize(self, seconds: float) -> None:
        """Drain the bucket after a rate-limit response (honors Retry-After)."""
        with self._lock:
            self._refill()
            self._tokens = min(self._tokens, 0.0) - seconds * self._rate
