"""Indicator frame for one timeframe, and a higher-timeframe view aligned to a lower one
without look-ahead (a higher bar is used only after it has closed)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_strategies.indicators import (
    adx,
    atr,
    bollinger,
    donchian_high,
    donchian_low,
    ema,
    macd,
    rolling_percentile,
    rsi,
)
from nexus_strategies.nqs1.config import Indicators

TF_RULE = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h",
           "H4": "4h", "D1": "1D", "W1": "W-SUN"}  # W1: Monday-Friday week, Sunday label


def resample(m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLC bars of ``tf`` from finer bars (label = bar open, left-closed)."""
    rule = TF_RULE[tf]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in m:
        agg["volume"] = "sum"
    out = m.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open"])


def compute(df: pd.DataFrame, p: Indicators | None = None) -> pd.DataFrame:
    p = p or Indicators()
    c = df["close"]
    out = df.copy()
    out["ema20"], out["ema50"], out["ema200"] = (ema(c, p.ema_fast), ema(c, p.ema_mid),
                                                 ema(c, p.ema_slow))
    out["rsi"] = rsi(c, p.rsi)
    m = macd(c, *p.macd)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    out["adx"] = adx(df, p.adx)
    out["atr"] = atr(df, p.atr)
    out["dc_high"], out["dc_low"] = donchian_high(df["high"], p.donchian), donchian_low(
        df["low"], p.donchian)
    bb = bollinger(c, p.bb, p.bb_k)
    out["bb_mid"], out["bb_up"], out["bb_low"], out["bb_width"] = (
        bb["mid"], bb["upper"], bb["lower"], bb["width"])
    out["mom"] = c / c.shift(p.momentum) - 1
    out["atr_pct"] = out["atr"] / c
    out["vol_pct"] = rolling_percentile(out["atr_pct"], p.vol_pct_window)
    out["width_pct"] = rolling_percentile(out["bb_width"], p.vol_pct_window)
    out["range_atr"] = (df["high"] - df["low"]) / out["atr"].shift(1)
    return out


def align_higher(lower: pd.DataFrame, higher: pd.DataFrame, higher_tf: str,
                 cols: tuple[str, ...]) -> pd.DataFrame:
    """Higher-timeframe columns on the lower index, using only CLOSED higher bars: a
    higher bar opening at t is usable from t + its length."""
    if higher_tf == "W1":
        avail = higher.index + pd.Timedelta(days=7)
    else:
        avail = higher.index + pd.Timedelta(TF_RULE[higher_tf])
    h = higher[list(cols)].copy()
    h.index = avail
    h = h[~h.index.duplicated(keep="last")].sort_index()
    out = h.reindex(lower.index, method="ffill")
    return out.add_prefix("htf_")


def trend_sign(row: pd.Series, prefix: str = "") -> int:
    """+1 bullish stack, -1 bearish stack, 0 otherwise (EMA 20/50/200 and price)."""
    e20, e50, e200, c = (row[f"{prefix}ema20"], row[f"{prefix}ema50"], row[f"{prefix}ema200"],
                         row[f"{prefix}close"])
    if np.isnan([e20, e50, e200, c]).any():
        return 0
    if c > e20 > e50 > e200:
        return 1
    if c < e20 < e50 < e200:
        return -1
    return 0
