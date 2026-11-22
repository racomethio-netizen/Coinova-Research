"""Strategy modules A-G of the deep-upgrade brief, built on the Phase C analysis engine.

Pre-registered in docs/research/2026-11-23-strategies-a-g-preregistration.md BEFORE any of
them was run on data. Long-only spot (futures are disabled, so the short sides are not
built). Every strategy starts disabled; it may trade only after passing the walk-forward
gates, and live money additionally needs a forward paper period and the owner's gates.

All rules use CLOSED bars only; entries happen at the next bar's open (backtester and
trader alike). The existing strategies (strategies.py, library_v2.py) are unchanged, so
their recorded validation results remain valid.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies import ta
from nexus_strategies.base import Strategy
from nexus_strategies.library_v2 import session_vwap


def _frame(df: pd.DataFrame, entry: pd.Series, exit_: pd.Series, stop: pd.Series,
           target: pd.Series, confidence: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop, "target": target,
                         "confidence": confidence}, index=df.index)


def _recent(flag: pd.Series, bars: int) -> pd.Series:
    """True if ``flag`` was true in any of the previous ``bars`` bars (excluding this one)."""
    return flag.fillna(False).astype(float).shift(1).rolling(bars, min_periods=1).max() == 1


class TrendSupertrend(Strategy):
    """A. Trend following: 1h pullback in an aligned uptrend, confirmed on 4h.

    Entry: 4h close > EMA50 and EMA20 > EMA50; 1h EMA9 > EMA21 > EMA50; ADX >= adx_min;
    Supertrend up; the previous bar's low touched EMA21 and this bar closed back above it
    with RSI between 50 and 70 and a positive MACD histogram. Stop: ATR stop. Exit
    (dynamic): Supertrend flips down."""

    code = "trend_supertrend"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"adx_min": 20.0, "stop_atr": 2.0, "target_atr": 4.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        c = df["close"]
        e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
        st = ta.supertrend(df)["direction"]
        r, hist = ind.rsi(c), ind.macd(c)["hist"]
        htf = (df["h_close"] > df["h_ema50"]) & (df["h_ema20"] > df["h_ema50"])
        aligned = (e9 > e21) & (e21 > e50)
        pullback = df["low"].shift(1) <= e21.shift(1)
        resume = (c > e21) & (c > df["open"])
        momentum = r.between(50, 70) & (hist > 0)
        strength = ta.dmi(df)["adx"]
        entry = htf & aligned & (strength >= p["adx_min"]) & (st > 0) & pullback & resume \
            & momentum
        conf = 0.5 + ((strength - p["adx_min"]) / 40).clip(0, 0.5)
        return _frame(df, entry, st < 0, c - p["stop_atr"] * df["atr"],
                      c + p["target_atr"] * df["atr"], conf)


class BreakoutRetest(Strategy):
    """B. Breakout from consolidation, entered on a successful retest (false-breakout
    filter: a breakout that closes back inside the range is discarded).

    Breakout bar: close above the previous ``channel`` bars' high (Donchian), the range was
    a consolidation (narrow in ATRs with low ADX) at some point in the 10 bars before,
    relative volume >= rv_min and ATR rising (expansion). Entry: within 5 bars a low
    returns to the broken level (within 0.25 ATR) and the bar closes above it, with no
    failed-breakout close in between; 4h close > EMA50. Stop below the retest low."""

    code = "breakout_retest"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"channel": 20.0, "rv_min": 1.5, "stop_atr": 0.5,
                                            "target_atr": 4.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        n = int(p["channel"])
        rs = ta.range_state(df, n)
        rv = ta.relative_volume(df["volume"])
        expanding = df["atr"] > df["atr"].shift(5)
        consolidated = _recent(rs["ranging"] == True, 10)  # noqa: E712 (object column)
        breakout = (rs["breakout_up"] == True) & consolidated & (rv >= p["rv_min"]) \
            & expanding  # noqa: E712
        follow = ta.breakout_follow_through(df, n=n, window=5)
        valid_bo = _recent(breakout, 5) & ~_recent(follow["failed_breakout_up"] == 1, 5)
        entry = (follow["retest_up"] == 1) & valid_bo & (df["h_close"] > df["h_ema50"])
        e20 = ind.ema(df["close"], 20)
        conf = (rv.clip(1, 3) - 1) / 2 * 0.5 + 0.5
        return _frame(df, entry, df["close"] < e20, df["low"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class MomentumRv(Strategy):
    """C. Momentum ignition with relative volume, traded only in an up-trend regime.

    Entry: regime trend_up; RSI crosses above rsi_cross; MACD histogram positive and
    rising; ROC(12) > 0; relative volume >= rv_min; 1h EMA20 > EMA50 and 1h RSI > 50.
    Exit: RSI < 45 or MACD histogram < 0."""

    code = "momentum_rv"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"rsi_cross": 55.0, "rv_min": 1.5, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        c = df["close"]
        r = ind.rsi(c)
        hist = ind.macd(c)["hist"]
        rv = ta.relative_volume(df["volume"])
        cross = (r > p["rsi_cross"]) & (r.shift(1) <= p["rsi_cross"])
        entry = (df["regime"] == "trend_up") & cross & (hist > 0) & (hist > hist.shift(1)) \
            & (ta.roc(c) > 0) & (rv >= p["rv_min"]) & (df["h_ema20"] > df["h_ema50"]) \
            & (df["h_rsi"] > 50)
        conf = 0.5 + (rv.clip(1, 4) - 1) / 6
        return _frame(df, entry, (r < 45) | (hist < 0), c - p["stop_atr"] * df["atr"],
                      c + p["target_atr"] * df["atr"], conf)


class MeanReversionVwap(Strategy):
    """D. Mean reversion to the session VWAP, only when the market is not trending.

    Entry: the previous close was below the lower Bollinger band (20, 2); RSI < rsi_max;
    VWAP deviation z-score (vs the previous 96 bars) <= -z_min; this bar closes up; 1h ADX
    < h_adx_max and 15m ADX < 25 (strong directional markets are excluded). Target: the
    VWAP (the mean). Exit: close at or above the Bollinger middle band."""

    code = "mean_reversion_vwap"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"rsi_max": 30.0, "z_min": 2.0, "h_adx_max": 25.0,
                                            "stop_atr": 2.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        c = df["close"]
        vwap = session_vwap(df)
        dev = c - vwap
        sd = dev.shift(1).rolling(96, min_periods=96).std()
        z = (dev - dev.shift(1).rolling(96, min_periods=96).mean()) / sd.replace(0, np.nan)
        bb = ind.bollinger(c)
        r = ind.rsi(c)
        calm = (df["h_adx"] < p["h_adx_max"]) & (ta.dmi(df)["adx"] < 25)
        entry = (c.shift(1) < bb["lower"].shift(1)) & (r < p["rsi_max"]) & (z <= -p["z_min"]) \
            & (c > df["open"]) & calm
        conf = (-z).clip(0, 4) / 4 * 0.5 + 0.5
        return _frame(df, entry, c >= bb["mid"], c - p["stop_atr"] * df["atr"], vwap, conf)


class VwapTrend(Strategy):
    """E. VWAP trend continuation: in a 1h uptrend, price held above the session VWAP,
    pulls back to it and closes back above with volume and momentum.

    Entry: at least 4 of the previous 6 closes above VWAP; this bar's low within
    ``touch_atr`` ATR of VWAP and close above VWAP and above its open; relative volume >= 1;
    RSI > 50; 1h EMA20 > EMA50. Session filter: no entries in the first 2 hours of the UTC
    day (the VWAP has too few bars). Exit: close below VWAP."""

    code = "vwap_trend"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"touch_atr": 0.3, "stop_atr": 1.5,
                                            "target_atr": 3.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        c = df["close"]
        vwap = session_vwap(df)
        above = (c > vwap).astype(float)
        held = above.shift(1).rolling(6, min_periods=6).sum() >= 4
        touch = df["low"] <= vwap + p["touch_atr"] * df["atr"]
        rv = ta.relative_volume(df["volume"])
        session_ok = pd.Series(df.index.hour >= 2, index=df.index)
        entry = held & touch & (c > vwap) & (c > df["open"]) & (rv >= 1) \
            & (ind.rsi(c) > 50) & (df["h_ema20"] > df["h_ema50"]) & session_ok
        conf = 0.5 + (rv.clip(1, 3) - 1) / 4
        return _frame(df, entry, c < vwap, c - p["stop_atr"] * df["atr"],
                      c + p["target_atr"] * df["atr"], conf)


class SweepReclaim(Strategy):
    """F. Liquidity sweep reversal with wick and volume confirmation.

    Entry: the bar trades below the last confirmed swing low and closes back above it
    (sweep + reclaim), its lower wick is >= wick_min of the bar range, relative volume >=
    rv_min, the close is bullish and 1h EMA20 >= EMA50. With ``require_equal`` = 1 the swept
    level must also be an equal-lows pool (within 0.1 ATR of a recent equal low).
    Invalidation: stop below the sweep wick. These are price patterns near likely stop
    levels, not evidence of institutional activity."""

    code = "sweep_reclaim"
    base_tf, confirm_tf = "15m", "1h"
    defaults: ClassVar[dict[str, float]] = {"wick_min": 0.5, "rv_min": 1.2,
                                            "require_equal": 0.0, "stop_atr": 0.3,
                                            "target_atr": 2.5}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        from nexus_analysis.sweeps import liquidity_sweeps

        p = self.params
        sw = liquidity_sweeps(df, left=2, right=2)
        shape = ta.candle_shape(df)
        rv = ta.relative_volume(df["volume"])
        eq_low = ta.equal_levels(df)["equal_low"].ffill()
        near_equal = (sw["swept_low_level"] - eq_low).abs() <= 0.1 * df["atr"]
        pool_ok = near_equal if p["require_equal"] >= 1 else pd.Series(True, index=df.index)
        entry = sw["sweep_low"] & (shape["lower_wick"] >= p["wick_min"]) \
            & (rv >= p["rv_min"]) & (df["close"] > df["open"]) \
            & (df["h_ema20"] >= df["h_ema50"]) & pool_ok
        conf = 0.5 + shape["lower_wick"].clip(0, 1) * 0.5
        return _frame(df, entry, df["close"] < df["low"].shift(1),
                      df["low"] - p["stop_atr"] * df["atr"],
                      df["close"] + p["target_atr"] * df["atr"], conf)


class MarketStructure(Strategy):
    """G. Market structure: break of structure (or change of character) upward, then a
    retest of the broken swing high that holds.

    Event: ``bos_up`` (``mode`` 0) or ``bos_up``/``choch_up`` (``mode`` 1) from confirmed
    swings; the broken level is the last swing high before the event. Entry: within
    ``window`` bars a low comes back to the level (within 0.25 ATR) and the bar closes above
    it; structure trend up; 4h EMA20 > EMA50. Stop: the last confirmed swing low
    (invalidation) minus 0.2 ATR, skipped when farther than 3 ATR. Target: entry + rr x
    risk. Exit: a ``choch_down`` event."""

    code = "market_structure"
    base_tf, confirm_tf = "1h", "4h"
    defaults: ClassVar[dict[str, float]] = {"mode": 0.0, "window": 5.0, "rr": 2.0}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        from nexus_analysis.structure import market_structure

        p = self.params
        ms = market_structure(df)
        events = ms["event"]
        kinds = ("bos_up",) if p["mode"] < 1 else ("bos_up", "choch_up")
        level_at_event = ms["last_swing_high"].shift(1).where(events.isin(kinds))
        a = df["atr"].to_numpy(float)
        lo, c = df["low"].to_numpy(float), df["close"].to_numpy(float)
        lv = level_at_event.to_numpy(float)
        n, window = len(df), int(p["window"])
        retest = np.zeros(n, dtype=bool)
        active: list[tuple[int, float]] = []
        for i in range(n):
            keep = []
            for start, level in active:
                if i - start > window or c[i] < level:      # expired or failed
                    continue
                if not np.isnan(a[i]) and lo[i] <= level + 0.25 * a[i] and c[i] > level:
                    retest[i] = True
                    continue
                keep.append((start, level))
            active = keep
            if not np.isnan(lv[i]):
                active.append((i, lv[i]))
        stop = ms["last_swing_low"] - 0.2 * df["atr"]
        risk = df["close"] - stop
        ok = (risk > 0) & (risk <= 3 * df["atr"])
        entry = pd.Series(retest, index=df.index) & (ms["trend"] == "up") \
            & (df["h_ema20"] > df["h_ema50"]) & ok
        conf = pd.Series(0.6, index=df.index)
        return _frame(df, entry, events == "choch_down", stop,
                      df["close"] + p["rr"] * risk, conf)


V3_STRATEGIES: dict[str, type[Strategy]] = {
    s.code: s for s in (TrendSupertrend, BreakoutRetest, MomentumRv, MeanReversionVwap,
                        VwapTrend, SweepReclaim, MarketStructure)}

# Walk-forward grids (pre-registered; parameters are chosen on TRAIN windows only).
V3_GRIDS: dict[str, dict[str, list[float]]] = {
    "trend_supertrend": {"adx_min": [18, 25], "stop_atr": [1.5, 2.5],
                         "target_atr": [3.0, 5.0], "use_exit": [0, 1]},
    "breakout_retest": {"channel": [20, 40], "rv_min": [1.2, 2.0], "stop_atr": [0.5, 1.0],
                        "target_atr": [3.0, 5.0], "use_exit": [0, 1]},
    "momentum_rv": {"rsi_cross": [55, 60], "rv_min": [1.5, 2.5], "stop_atr": [1.5, 2.5],
                    "target_atr": [3.0, 4.5], "use_exit": [0, 1]},
    "mean_reversion_vwap": {"rsi_max": [25, 30], "z_min": [1.5, 2.5], "h_adx_max": [20, 28],
                            "stop_atr": [1.5, 2.5], "use_exit": [0, 1]},
    "vwap_trend": {"touch_atr": [0.2, 0.5], "stop_atr": [1.0, 2.0], "target_atr": [2.5, 4.0],
                   "use_exit": [0, 1]},
    "sweep_reclaim": {"wick_min": [0.4, 0.6], "rv_min": [1.0, 1.5], "require_equal": [0, 1],
                      "stop_atr": [0.2, 0.5], "target_atr": [2.0, 3.0]},
    "market_structure": {"mode": [0, 1], "window": [5, 10], "rr": [2.0, 3.0],
                         "use_exit": [0, 1]},
}
