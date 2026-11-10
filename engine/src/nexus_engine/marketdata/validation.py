"""Candle validation (docs/nexus/SIGNAL_ENGINE.md §1). Reject and report; never repair."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..timeframes import close_time_for, is_aligned
from .types import Candle


@dataclass(frozen=True, slots=True)
class Rejection:
    open_time: datetime
    reasons: tuple[str, ...]


def candle_problems(c: Candle, tf: str, now: datetime) -> list[str]:
    problems: list[str] = []
    if c.open_time.tzinfo is None or c.close_time.tzinfo is None:
        return ["naive_timestamp"]
    numbers = [c.open, c.high, c.low, c.close, c.volume] + ([c.quote_volume] if c.quote_volume is not None else [])
    if not all(v.is_finite() for v in numbers):
        return ["non_finite_value"]  # NaN/Infinity cannot be compared safely; reject outright
    if not is_aligned(c.open_time, tf):
        problems.append("misaligned_open_time")
    if c.close_time != close_time_for(c.open_time, tf):
        problems.append("unexpected_close_time")
    if c.open_time > now:
        problems.append("future_open_time")
    if c.is_final and c.close_time >= now:
        problems.append("final_before_close")
    if min(c.open, c.high, c.low, c.close) <= 0:
        problems.append("non_positive_price")
    if c.volume < 0 or (c.quote_volume is not None and c.quote_volume < 0):
        problems.append("negative_volume")
    if c.trade_count is not None and c.trade_count < 0:
        problems.append("negative_trade_count")
    if not (c.low <= min(c.open, c.close) and max(c.open, c.close) <= c.high):
        problems.append("inconsistent_ohlc")
    return problems


def partition(candles: Iterable[Candle], tf: str, now: datetime) -> tuple[list[Candle], list[Rejection]]:
    """Split into valid candles (deduplicated by open time, first wins) and rejections."""
    valid: list[Candle] = []
    rejected: list[Rejection] = []
    seen: set[datetime] = set()
    for c in candles:
        problems = candle_problems(c, tf, now)
        if c.open_time in seen:
            problems.append("duplicate_in_batch")
        if problems:
            rejected.append(Rejection(c.open_time, tuple(problems)))
            continue
        seen.add(c.open_time)
        valid.append(c)
    valid.sort(key=lambda c: c.open_time)
    return valid, rejected
