"""Phase 6 strategies, pre-registered in docs/research/2026-11-22-phase6-preregistration.md.

A-C use only candles (live-capable). D and E need extra columns that the research loader
adds (``funding_24h`` and ``taker_buy_ratio``); without them they never signal. All are
long-only spot, start disabled, and trade only after passing the same validation gates.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.base import Strategy


def _frame(df: pd.DataFrame, entry: pd.Series, exit_: pd.Series, stop: pd.Series,
           target: pd.Series, confidence: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop, "target": target,
                         "confidence": confidence}, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """UTC-day anchored VWAP of typical price (causal: cumulative within the day)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.floor("D")
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return pv / vol.where(vol > 0)


class VwapReclaim(Strategy):
    """A. 15m reclaim of the session VWAP after a stretch below it, 1h trend up."""

    code = "vwap_reclaim"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"below_bars": 4.0, "vol_z_min": 0.5,
                                            "stop_atr": 1.5, "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        vwap = session_vwap(df)
        below = (df["close"] < vwap).astype(float)
        n = int(p["below_bars"])
        was_below = below.shift(1).rolling(n, min_periods=n).min() == 1
        reclaim = (df["close"] > vwap) & (df["close"] > df["open"])
        vz = ind.volume_z(df["volume"], 50)
        entry = was_below & reclaim & (vz >= p["vol_z_min"]) & (df["h_close"] > df["h_ema50"])
        conf = (vz.clip(0, 3) / 3) * 0.6 + 0.4
        return _frame(df, entry, df["close"] < vwap, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class VolatilityExpansion(Strategy):
    """B. 1h Bollinger squeeze followed by a close above the upper band, ATR rising,
    4h trend up."""

    code = "volatility_expansion"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"squeeze_pct": 0.1, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        bb = ind.bollinger(df["close"], 20, 2.0)
        width_rank = ind.rolling_percentile(bb["width"], 200, min_periods=200)
        squeezed = (width_rank <= p["squeeze_pct"]).astype(float).rolling(5).max() == 1
        expand = (df["close"] > bb["upper"]) & (df["atr"] > df["atr"].shift(3))
        entry = squeezed.shift(1).fillna(False).astype(bool) & expand & (
            df["h_ema20"] > df["h_ema50"])
        conf = pd.Series(0.6, index=df.index)
        return _frame(df, entry, df["close"] < bb["mid"], df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class MtfConfluence(Strategy):
    """C. 4h and 1h trend stacks aligned; entry when the 1h MACD histogram crosses up."""

    code = "mtf_confluence"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"h_rsi_max": 70.0, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        e20, e50 = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
        hist = ind.macd(df["close"])["hist"]
        r = ind.rsi(df["close"], 14)
        htf = (df["h_close"] > df["h_ema20"]) & (df["h_ema20"] > df["h_ema50"]) & (
            df["h_rsi"] >= 50) & (df["h_rsi"] <= p["h_rsi_max"])
        ltf = (df["close"] > e20) & (e20 > e50) & (hist > 0) & (hist.shift(1) <= 0) & (r < 70)
        conf = ((df["h_rsi"] - 50) / 20).clip(0, 1) * 0.5 + 0.5
        return _frame(df, htf & ltf, hist < 0, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class FundingSqueeze(Strategy):
    """D. Shorts crowded (24h mean funding <= funding_max) and price reclaims EMA20.
    Needs ``funding_24h`` (mean of settlements BEFORE the bar) in the 1h frame."""

    code = "funding_squeeze"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"funding_max": -0.00005, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        e20, e50 = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
        f = df["funding_24h"] if "funding_24h" in df else pd.Series(float("nan"), df.index)
        cross = (df["close"] > e20) & (df["close"].shift(1) <= e20.shift(1))
        entry = (f <= p["funding_max"]) & cross
        conf = (-f / 0.0005).clip(0, 1).fillna(0) * 0.5 + 0.5
        return _frame(df, entry, df["close"] < e50, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class TakerFlow(Strategy):
    """E. Aggressive buying (3h mean perp taker-buy share >= ratio_min) with price above
    EMA20 and above-normal volume. Needs ``taker_buy_ratio`` in the 1h frame."""

    code = "taker_flow"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"ratio_min": 0.55, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        e20 = ind.ema(df["close"], 20)
        tr = df["taker_buy_ratio"] if "taker_buy_ratio" in df else pd.Series(float("nan"),
                                                                              df.index)
        flow = tr.rolling(3, min_periods=3).mean()
        vz = ind.volume_z(df["volume"], 50)
        entry = (flow >= p["ratio_min"]) & (df["close"] > e20) & (vz >= 1)
        conf = ((flow - 0.5) / 0.2).clip(0, 1).fillna(0) * 0.5 + 0.5
        return _frame(df, entry, df["close"] < e20, df["close"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


CANDLE_STRATEGIES: dict[str, type[Strategy]] = {
    s.code: s for s in (VwapReclaim, VolatilityExpansion, MtfConfluence)}
RESEARCH_STRATEGIES: dict[str, type[Strategy]] = {
    s.code: s for s in (FundingSqueeze, TakerFlow)}
