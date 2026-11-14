"""Rule-based market regime classification (causal).

Regimes: ``trend_up``, ``trend_down``, ``range``, ``high_vol`` (volatility in the top
decile of its trailing year: risk is reduced/blocked regardless of direction).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind

REGIMES = ("trend_up", "trend_down", "range", "high_vol")


def classify(df: pd.DataFrame, *, adx_trend: float = 22.0, vol_window: int = 48,
             vol_rank_window: int = 2000, high_vol_pct: float = 0.9) -> pd.Series:
    close = df["close"]
    ema_fast, ema_slow = ind.ema(close, 20), ind.ema(close, 50)
    strength = ind.adx(df, 14)
    vol_rank = ind.rolling_percentile(ind.realized_vol(close, vol_window), vol_rank_window,
                                      min_periods=200)
    regime = np.where(strength >= adx_trend,
                      np.where(ema_fast > ema_slow, "trend_up", "trend_down"), "range")
    regime = np.where(vol_rank >= high_vol_pct, "high_vol", regime)
    out = pd.Series(regime, index=df.index, dtype="object")
    out[strength.isna() | ema_slow.isna()] = None
    return out
