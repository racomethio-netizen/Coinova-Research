"""Exponential backoff with jitter, honoring Retry-After."""

from __future__ import annotations

import random

MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.0
MAX_DELAY_S = 60.0


def backoff_delay(attempt: int, retry_after_s: float | None = None, rng: random.Random | None = None) -> float:
    """Delay before retry number `attempt` (1-based)."""
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    r = rng or random.Random()  # noqa: S311 - jitter, not security
    exp = min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (attempt - 1))
    delay = exp * (0.5 + r.random() / 2)
    if retry_after_s is not None:
        delay = max(delay, min(retry_after_s, MAX_DELAY_S * 5))
    return float(delay)


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
