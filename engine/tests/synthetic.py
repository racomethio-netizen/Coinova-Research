"""Synthetic market series for tests only (never shown to users or stored outside test DBs)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from nexus_engine.marketdata.types import Candle
from nexus_engine.timeframes import close_time_for, tf_delta

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def random_walk(tf: str, n: int, seed: int, start: datetime = T0, regime_len: int = 200, vol: float = 0.006) -> list[Candle]:
    """Random walk with alternating up/down drift regimes, so trends and pullbacks occur."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-1, 1], size=n // regime_len + 1) * vol * 0.25, regime_len)[:n]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, vol, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.lognormal(3, 0.6, n)
    out = []
    for i in range(n):
        t = start + tf_delta(tf) * i
        q = lambda x: Decimal(f"{x:.8f}")  # noqa: E731
        out.append(Candle(t, close_time_for(t, tf), q(open_[i]), q(high[i]), q(low[i]), q(close[i]), q(volume[i]), None, None, True))
    return out


def aggregate(candles: list[Candle], tf_from: str, tf_to: str) -> list[Candle]:
    """Aggregate complete higher-timeframe candles from lower-timeframe ones."""
    k = int(tf_delta(tf_to) / tf_delta(tf_from))
    out = []
    for i in range(0, len(candles) - k + 1, k):
        grp = candles[i : i + k]
        t = grp[0].open_time
        out.append(
            Candle(
                t,
                close_time_for(t, tf_to),
                grp[0].open,
                max(c.high for c in grp),
                min(c.low for c in grp),
                grp[-1].close,
                sum((c.volume for c in grp), Decimal(0)),
                None,
                None,
                True,
            )
        )
    return out
