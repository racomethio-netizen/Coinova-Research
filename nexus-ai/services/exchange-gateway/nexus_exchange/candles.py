"""Candle aggregation for timeframes an exchange does not serve natively."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from nexus_exchange.models import Candle


def aggregate_candles(candles: Sequence[Candle], *, source_seconds: int,
                      target_seconds: int) -> list[Candle]:
    """Aggregate closed source candles into target buckets aligned to the UTC epoch.

    A bucket is emitted only if EVERY constituent source candle is present; incomplete
    buckets (exchange gaps, or the still-forming bucket) are dropped, never padded.
    """
    if target_seconds % source_seconds:
        raise ValueError("target timeframe must be a multiple of the source timeframe")
    factor = target_seconds // source_seconds
    buckets: dict[int, list[Candle]] = {}
    for c in candles:
        ts = int(c.open_time.timestamp())
        if ts % source_seconds:
            raise ValueError(f"source candle not aligned: {c.open_time.isoformat()}")
        buckets.setdefault(ts - ts % target_seconds, []).append(c)
    out: list[Candle] = []
    for start in sorted(buckets):
        group = sorted(buckets[start], key=lambda c: c.open_time)
        first = group[0].open_time - timedelta(seconds=int(group[0].open_time.timestamp()) - start)
        expected = [first + timedelta(seconds=i * source_seconds) for i in range(factor)]
        if [c.open_time for c in group] != expected:
            continue
        out.append(Candle(
            open_time=first,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=sum((c.volume for c in group), start=group[0].volume * 0),
        ))
    return out
