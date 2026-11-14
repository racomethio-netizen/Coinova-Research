"""The four NEXUS AI strategies (long-only spot, multi-timeframe confirmed)."""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.base import Strategy


def _frame(df: pd.DataFrame, entry: pd.Series, exit_: pd.Series, stop: pd.Series,
           target: pd.Series, confidence: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop, "target": target,
                         "confidence": confidence}, index=df.index)


class TrendFollowing(Strategy):
    """1h pullback-and-resume in an established uptrend confirmed on 4h.

    Entry: 4h close > 4h EMA50 and 4h EMA20 > EMA50; 1h EMA20 > EMA50 with ADX above
    threshold; the previous bar's low touched the 1h EMA20 and this bar closed back
    above it. Exit: 1h close below EMA50.
    """

    code = "trend_following"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"adx_min": 20.0, "stop_atr": 2.0, "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        e20, e50 = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
        strength = ind.adx(df, 14)
        htf_up = (df["h_close"] > df["h_ema50"]) & (df["h_ema20"] > df["h_ema50"])
        pullback = df["low"].shift(1) <= e20.shift(1)
        resume = (df["close"] > e20) & (df["close"] > df["open"])
        entry = htf_up & (e20 > e50) & (strength >= p["adx_min"]) & pullback & resume
        conf = ((strength - p["adx_min"]) / 30).clip(0, 1) * 0.5 + 0.5 * htf_up.astype(float)
        return _frame(df, entry, df["close"] < e50, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class Momentum(Strategy):
    """15m momentum ignition confirmed by a 1h uptrend.

    Entry: RSI(14) crosses up through the threshold, MACD histogram positive and rising,
    volume above its recent norm; 1h close > 1h EMA50 and 1h RSI > 50.
    Exit: RSI falls below 45 or MACD histogram turns negative.
    """

    code = "momentum"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {
        "rsi_cross": 55.0, "vol_z_min": 1.0, "stop_atr": 1.5, "target_atr": 2.5}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        r = ind.rsi(df["close"], 14)
        m = ind.macd(df["close"])
        vz = ind.volume_z(df["volume"], 50)
        cross = (r >= p["rsi_cross"]) & (r.shift(1) < p["rsi_cross"])
        accel = (m["hist"] > 0) & (m["hist"] > m["hist"].shift(1))
        htf = (df["h_close"] > df["h_ema50"]) & (df["h_rsi"] > 50)
        entry = cross & accel & (vz >= p["vol_z_min"]) & htf & (r < 75)
        conf = (vz.clip(0, 4) / 4) * 0.5 + ((df["h_rsi"] - 50) / 30).clip(0, 1) * 0.5
        exit_ = (r < 45) | (m["hist"] < 0)
        return _frame(df, entry, exit_, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class Breakout(Strategy):
    """15m Donchian breakout out of a volatility squeeze, 1h trend aligned.

    Entry: close above the prior N-bar high, Bollinger width in the bottom quantile of
    its trailing window during the prior bar (squeeze), volume expansion, 1h EMA20 >
    EMA50. Exit: close back below the breakout channel midpoint.
    """

    code = "breakout"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {
        "channel": 20.0, "squeeze_pct": 0.25, "vol_z_min": 1.5, "stop_atr": 1.5, "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        n = int(p["channel"])
        hi, lo = ind.donchian_high(df["high"], n), ind.donchian_low(df["low"], n)
        width = ind.bollinger(df["close"], 20)["width"]
        squeeze = ind.rolling_percentile(width, 200).shift(1) <= p["squeeze_pct"]
        vz = ind.volume_z(df["volume"], 50)
        htf = df["h_ema20"] > df["h_ema50"]
        entry = (df["close"] > hi) & squeeze & (vz >= p["vol_z_min"]) & htf
        conf = (vz.clip(0, 5) / 5) * 0.6 + 0.4 * htf.astype(float)
        return _frame(df, entry, df["close"] < (hi + lo) / 2,
                      df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class MeanReversion(Strategy):
    """15m oversold snap-back, only when the 1h market is ranging (low ADX).

    Entry: previous bar closed below the lower Bollinger band with RSI below the
    threshold and this bar closed back inside the band; 1h ADX below the range
    threshold. Target: Bollinger middle band. Exit: close above the middle band.
    """

    code = "mean_reversion"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"rsi_max": 30.0, "h_adx_max": 20.0, "stop_atr": 1.5}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        bb = ind.bollinger(df["close"], 20, 2.0)
        r = ind.rsi(df["close"], 14)
        washed = (df["close"].shift(1) < bb["lower"].shift(1)) & (r.shift(1) < p["rsi_max"])
        back_inside = df["close"] > bb["lower"]
        ranging = df["h_adx"] < p["h_adx_max"]
        entry = washed & back_inside & ranging
        conf = ((p["rsi_max"] - r.shift(1)) / 20).clip(0, 1) * 0.5 + \
            ((p["h_adx_max"] - df["h_adx"]) / p["h_adx_max"]).clip(0, 1) * 0.5
        target = bb["mid"].where(bb["mid"] > df["close"], np.nan)
        return _frame(df, entry, df["close"] >= bb["mid"],
                      df["close"] - p["stop_atr"] * df["atr"], target, conf)


ALL_STRATEGIES: dict[str, type[Strategy]] = {
    s.code: s for s in (TrendFollowing, Momentum, Breakout, MeanReversion)}
