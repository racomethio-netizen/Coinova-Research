"""Pure market-data analytics: gap detection, order book metrics, volatility.

Analytics use float where the result is a statistic (volatility, imbalance); prices and
quantities that feed orders stay Decimal elsewhere.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from nexus_exchange.models import Candle, OrderBook


# ------------------------------------------------------------------ gaps
@dataclass(frozen=True)
class Gap:
    start: datetime  # first missing open_time
    end: datetime    # last missing open_time
    missing: int


def detect_gaps(open_times: Sequence[datetime], step_seconds: int, *,
                start: datetime | None = None, end: datetime | None = None) -> list[Gap]:
    """Runs of missing bars between ``start`` and ``end`` (inclusive open times).
    Defaults to the first/last present bar."""
    step = timedelta(seconds=step_seconds)
    present = set(open_times)
    if not present and (start is None or end is None):
        return []
    lo = start if start is not None else min(present)
    hi = end if end is not None else max(present)
    gaps: list[Gap] = []
    run_start: datetime | None = None
    t = lo
    while t <= hi:
        if t not in present:
            run_start = run_start or t
        elif run_start is not None:
            gaps.append(Gap(run_start, t - step, int((t - run_start) / step)))
            run_start = None
        t += step
    if run_start is not None:
        gaps.append(Gap(run_start, t - step, int((t - run_start) / step)))
    return gaps


# ------------------------------------------------------------------ order book
@dataclass(frozen=True)
class BookMetrics:
    best_bid: Decimal
    best_ask: Decimal
    mid: Decimal
    spread_bps: Decimal
    bid_depth_quote: Decimal  # quote value of bids within band_bps of mid
    ask_depth_quote: Decimal
    imbalance: float          # (bid - ask) / (bid + ask) depth, in [-1, 1]


def book_metrics(book: OrderBook, *, band_bps: Decimal = Decimal(50)) -> BookMetrics | None:
    if book.best_bid is None or book.best_ask is None or book.mid is None:
        return None
    mid = book.mid
    lo = mid * (1 - band_bps / Decimal(10_000))
    hi = mid * (1 + band_bps / Decimal(10_000))
    bid_depth = sum((p * a for p, a in book.bids if p >= lo), Decimal(0))
    ask_depth = sum((p * a for p, a in book.asks if p <= hi), Decimal(0))
    total = bid_depth + ask_depth
    imbalance = float((bid_depth - ask_depth) / total) if total > 0 else 0.0
    assert book.spread_bps is not None
    return BookMetrics(book.best_bid, book.best_ask, mid, book.spread_bps, bid_depth,
                       ask_depth, imbalance)


def estimate_fill(book: OrderBook, side: str, amount: Decimal) -> tuple[Decimal, Decimal] | None:
    """Walk the book: (volume-weighted average price, filled amount) for a marketable
    order of ``amount``. Filled < amount means the visible book was too thin."""
    levels = book.asks if side == "buy" else book.bids
    remaining, cost = amount, Decimal(0)
    for price, qty in levels:
        take = min(remaining, qty)
        cost += take * price
        remaining -= take
        if remaining <= 0:
            break
    filled = amount - remaining
    if filled <= 0:
        return None
    return cost / filled, filled


# ------------------------------------------------------------------ volatility
def log_returns(closes: Sequence[Decimal]) -> list[float]:
    return [math.log(float(b) / float(a)) for a, b in pairwise(closes)
            if a > 0 and b > 0]


def realized_volatility(candles: Sequence[Candle], *, step_seconds: int,
                        annualize: bool = True) -> float | None:
    rets = log_returns([c.close for c in candles])
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    vol = math.sqrt(var)
    return vol * math.sqrt(365 * 24 * 3600 / step_seconds) if annualize else vol


def average_true_range(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    """Wilder ATR over the last ``period`` true ranges."""
    if len(candles) < period + 1:
        return None
    trs: list[Decimal] = []
    for prev, cur in pairwise(candles):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    atr = sum(trs[:period], Decimal(0)) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def volume_zscore(candles: Sequence[Candle], lookback: int = 50) -> float | None:
    if len(candles) < lookback + 1:
        return None
    hist = [float(c.volume) for c in candles[-lookback - 1:-1]]
    mean = sum(hist) / len(hist)
    sd = math.sqrt(sum((v - mean) ** 2 for v in hist) / (len(hist) - 1))
    if sd == 0:
        return None
    return (float(candles[-1].volume) - mean) / sd
