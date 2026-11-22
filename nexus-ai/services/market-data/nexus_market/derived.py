"""Derived timeframes and data-quality reporting, from STORED closed candles only.

* ``derive``: builds 3m from 1m, 30m from 15m and 2h/6h/12h from 1h with
  ``nexus_exchange.candles.aggregate_candles`` (epoch-aligned like Binance; a bar is written
  only when every constituent source bar exists, so a gap in the source is a gap in the
  result, never an estimate). No exchange calls.
* ``quality_report``: per pair and timeframe, newest bar, its age, coverage and the largest
  gap over a recent window, and whether the data is stale.
* ``prune_minutes``: rolling retention for 1m/3m bars (they are large and short-lived).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from nexus_common.timeutil import utcnow
from nexus_config import DERIVED_TIMEFRAMES, TIMEFRAME_SECONDS
from nexus_db import models, repo
from nexus_exchange.candles import aggregate_candles
from nexus_exchange.models import Candle
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from nexus_market.analytics import detect_gaps

MINUTE_TIMEFRAMES = ("1m", "3m")
MAX_SOURCE_ROWS = 3000        # source bars per derive() call (memory bound)
QUALITY_WINDOW_BARS = 1000
STALE_FACTOR = 2            # stale when the newest closed bar is > 2 bars + grace late
STALE_GRACE = timedelta(minutes=3)


def derive(s: Session, pair_id: int, target: str, *, now: datetime | None = None) -> int:
    """Write complete ``target`` bars from the stored source timeframe; returns how many
    bars were written (re-writing the newest bucket is idempotent)."""
    source = DERIVED_TIMEFRAMES[target]
    step = timedelta(seconds=TIMEFRAME_SECONDS[target])
    latest = repo.latest_candle_time(s, pair_id, target)
    newest = s.scalar(select(func.max(models.MarketCandle.open_time)).where(
        models.MarketCandle.pair_id == pair_id, models.MarketCandle.timeframe == source,
        models.MarketCandle.is_closed.is_(True)))
    if newest is None:
        return 0
    start = latest
    if start is None:
        start = s.scalar(select(func.min(models.MarketCandle.open_time)).where(
            models.MarketCandle.pair_id == pair_id, models.MarketCandle.timeframe == source,
            models.MarketCandle.is_closed.is_(True)))
    assert start is not None
    span = max(timedelta(seconds=TIMEFRAME_SECONDS[source] * MAX_SOURCE_ROWS), 2 * step)
    cutoff = now or utcnow()
    # At most MAX_SOURCE_ROWS source bars in memory at a time (512 MB host). A window with no
    # complete bucket (a gap in the source) is skipped so the job never gets stuck.
    while start <= newest:
        rows = repo.load_candles(s, pair_id, source, start=start, end=start + span)
        candles = [Candle(r.open_time, r.open, r.high, r.low, r.close, r.volume) for r in rows]
        built = aggregate_candles(candles, source_seconds=TIMEFRAME_SECONDS[source],
                                  target_seconds=TIMEFRAME_SECONDS[target])
        built = [c for c in built if c.open_time + step <= cutoff]   # closed buckets only
        newer = [c for c in built if latest is None or c.open_time > latest]
        if newer or start + span > newest:
            if not built:
                return 0
            return repo.upsert_candles(s, pair_id, target, built, source=f"derived:{source}")
        start += span - step          # overlap one bucket: none is split between windows
    return 0


@dataclass(frozen=True)
class SeriesQuality:
    exchange: str
    symbol: str
    timeframe: str
    source: str                 # exchange | derived from <tf>
    newest: str | None          # open time of the newest closed bar
    age_seconds: int | None     # since that bar CLOSED
    stale: bool
    window_bars: int            # bars expected in the window
    present: int
    missing: int
    largest_gap_bars: int
    coverage: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def series_quality(s: Session, exchange: str, symbol: str, pair_id: int, tf: str, *,
                   now: datetime | None = None) -> SeriesQuality:
    now = now or utcnow()
    secs = TIMEFRAME_SECONDS[tf]
    step = timedelta(seconds=secs)
    source = f"derived from {DERIVED_TIMEFRAMES[tf]}" if tf in DERIVED_TIMEFRAMES else "exchange"
    window_start = now - step * QUALITY_WINDOW_BARS
    times = list(s.scalars(select(models.MarketCandle.open_time).where(
        models.MarketCandle.pair_id == pair_id, models.MarketCandle.timeframe == tf,
        models.MarketCandle.is_closed.is_(True),
        models.MarketCandle.open_time >= window_start).order_by(models.MarketCandle.open_time)))
    newest = s.scalar(select(func.max(models.MarketCandle.open_time)).where(
        models.MarketCandle.pair_id == pair_id, models.MarketCandle.timeframe == tf,
        models.MarketCandle.is_closed.is_(True)))
    if newest is None:
        return SeriesQuality(exchange, symbol, tf, source, None, None, True, 0, 0, 0, 0, None)
    age = now - (newest + step)
    first = times[0] if times else newest
    # Expected bars from the first bar in the window to the newest closed one.
    expected = int((newest - first) / step) + 1 if times else 0
    gaps = detect_gaps(times, secs, start=first, end=newest) if times else []
    missing = sum(g.missing for g in gaps)
    largest = max((g.missing for g in gaps), default=0)
    stale = age > step * STALE_FACTOR + STALE_GRACE
    return SeriesQuality(exchange, symbol, tf, source, newest.isoformat(),
                         max(0, int(age.total_seconds())), stale, expected, len(times),
                         missing, largest, round(len(times) / expected, 4) if expected else None)


def quality_report(s: Session, pairs: list[tuple[str, str]], timeframes: list[str], *,
                   now: datetime | None = None) -> list[SeriesQuality]:
    out = []
    for exchange, symbol in pairs:
        try:
            pid = repo.pair_id(s, exchange, symbol)
        except LookupError:      # pair not synced on this exchange: nothing to report
            continue
        for tf in timeframes:
            out.append(series_quality(s, exchange, symbol, pid, tf, now=now))
    return out


def prune_minutes(s: Session, retention_days: int, *, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    res = s.execute(delete(models.MarketCandle).where(
        models.MarketCandle.timeframe.in_(MINUTE_TIMEFRAMES),
        models.MarketCandle.open_time < cutoff))
    return int(getattr(res, "rowcount", 0) or 0)
