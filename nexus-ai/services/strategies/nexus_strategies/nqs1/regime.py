"""Seven market regimes per bar, from indicators known at that bar's close.

Priority, first match wins:
1. UNSTABLE: volatility at its extreme (ATR% percentile >= 0.97) or a shock bar (range
   >= 3 ATR). No new trades.
2. VOL_EXPANSION: ATR% percentile >= 0.80, ATR up more than 15% in 5 bars, ADX >= 18.
3. STRONG_BULL / STRONG_BEAR: ADX >= 25 with an aligned EMA 20/50/200 stack, price on
   the trend side of EMA 20, and MACD histogram agreeing.
4. VOL_CONTRACTION: Bollinger band width in its lowest 15% (a squeeze).
5. SIDEWAYS: ADX < 18 and price inside the Donchian channel.
6. WEAK_TREND: everything else (uncertain, so NO_TRADE).

INSUFFICIENT_DATA is used while any indicator is still warming up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_strategies.nqs1.config import Regime

REGIMES = ("STRONG_BULL", "STRONG_BEAR", "WEAK_TREND", "SIDEWAYS", "VOL_EXPANSION",
           "VOL_CONTRACTION", "UNSTABLE", "INSUFFICIENT_DATA")
NEEDED = ("ema200", "adx", "atr", "vol_pct", "width_pct", "dc_high", "macd_hist")


def classify(f: pd.DataFrame, r: Regime | None = None) -> pd.Series:
    r = r or Regime()
    c = f["close"]
    unstable = (f["vol_pct"] >= r.vol_unstable) | (f["range_atr"] >= r.shock_atr)
    expansion = (f["vol_pct"] >= r.vol_expansion) & (f["atr"] > 1.15 * f["atr"].shift(5)) & \
        (f["adx"] >= r.adx_weak)
    bull = (f["adx"] >= r.adx_strong) & (c > f["ema20"]) & (f["ema20"] > f["ema50"]) & \
        (f["ema50"] > f["ema200"]) & (f["macd_hist"] > 0)
    bear = (f["adx"] >= r.adx_strong) & (c < f["ema20"]) & (f["ema20"] < f["ema50"]) & \
        (f["ema50"] < f["ema200"]) & (f["macd_hist"] < 0)
    contraction = f["width_pct"] <= r.squeeze
    sideways = (f["adx"] < r.adx_weak) & (c <= f["dc_high"]) & (c >= f["dc_low"])
    out = np.select([unstable, expansion, bull, bear, contraction, sideways],
                    ["UNSTABLE", "VOL_EXPANSION", "STRONG_BULL", "STRONG_BEAR",
                     "VOL_CONTRACTION", "SIDEWAYS"], default="WEAK_TREND")
    missing = f[list(NEEDED)].isna().any(axis=1).to_numpy()
    return pd.Series(np.where(missing, "INSUFFICIENT_DATA", out), index=f.index)


# Which strategies a regime allows (the owner's specification).
SELECTION: dict[str, tuple[str, ...]] = {
    "STRONG_BULL": ("trend",),
    "STRONG_BEAR": ("trend",),
    "VOL_EXPANSION": ("breakout",),
    "VOL_CONTRACTION": ("breakout",),     # a squeeze breakout, with stricter confirmation
    "SIDEWAYS": ("mean_reversion",),
    "WEAK_TREND": (),                     # uncertain: NO_TRADE
    "UNSTABLE": (),                       # stop trading
    "INSUFFICIENT_DATA": (),
}
