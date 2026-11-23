"""Global strategy library (owner's master command 2026-11-23, section 2).

Eight independent long-only spot strategies. Each has its OWN entry, exit, ATR stop,
target and time barrier (Hummingbot's "triple barrier"). Indicators are not pooled into
one score. Rules are standard public definitions:
* TradingView built-in strategy templates (MACD, RSI, Bollinger Bands, Channel Breakout);
* Freqtrade's sample strategy (RSI / Bollinger / MACD);
* the Turtle-style Donchian breakout.
Parameters are fixed BEFORE testing (docs/research/2026-11-23-global-library-
preregistration.md); nothing is fitted to the test data.

Causality: every rule uses CLOSED bars only. The decision is made at a bar's close and
executed at the next bar's open. Higher-timeframe values come from higher bars that had
closed by then (``htf_align``).

``signals(df, htf)`` returns, per base bar:
* ``entry``, ``exit`` (bool);
* ``stop``, ``target`` (price levels from this bar's close);
* ``time_limit`` (bars to hold at most).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies import ta

TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400, "1w": 604_800}
HIGHER = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
RULES = {"1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON"}


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLCV of COMPLETE ``tf`` bars from a finer frame (UTC, Binance-aligned: weeks start
    Monday 00:00). The last bar is dropped unless its period is fully covered."""
    g = df.resample(RULES[tf], label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"}).dropna()
    step = df.index.to_series().diff().median()
    if pd.notna(step) and step > pd.Timedelta(0):
        expected = pd.Timedelta(seconds=TF_SECONDS[tf]) / step
        sizes = g.size().reindex(out.index)
        # Weeks and days with a few missing source bars are kept; a partial LAST bar is not.
        if len(out) and sizes.iloc[-1] < expected:
            out = out.iloc[:-1]
    return out


def htf_align(base: pd.DataFrame, base_tf: str, higher: pd.DataFrame, higher_tf: str
              ) -> pd.DataFrame:
    """Higher-timeframe columns for each base bar, from higher bars CLOSED by the base
    bar's close."""
    left = pd.DataFrame({"_t": base.index + pd.Timedelta(seconds=TF_SECONDS[base_tf])})
    right = higher.reset_index(drop=True)
    right["_t"] = higher.index + pd.Timedelta(seconds=TF_SECONDS[higher_tf])
    merged = pd.merge_asof(left, right.sort_values("_t"), on="_t", direction="backward")
    merged.index = base.index
    return merged.drop(columns=["_t"])


def _out(df: pd.DataFrame, entry: pd.Series, exit_: pd.Series, stop: pd.Series,
         target: pd.Series, time_limit: int) -> pd.DataFrame:
    c = df["close"]
    valid = stop.notna() & target.notna() & (stop < c) & (target > c)
    return pd.DataFrame({"entry": entry.fillna(False).astype(bool) & valid,
                         "exit": exit_.fillna(False).astype(bool),
                         "stop": stop, "target": target,
                         "time_limit": time_limit}, index=df.index)


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    prev_b = b.shift(1) if isinstance(b, pd.Series) else b
    return (a > b) & (a.shift(1) <= prev_b)


def _htf_up(htf: pd.DataFrame) -> pd.Series:
    """Higher-timeframe uptrend: its close above its EMA50 (closed higher bars)."""
    return htf["close"] > htf["ema50"]


def htf_frame(higher: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"close": higher["close"], "ema50": ind.ema(higher["close"], 50)})


# ------------------------------------------------------------------------- A. trend
def ema_adx_macd_trend(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """A. EMA 20/50/200 stack, ADX >= 25, MACD line crosses above its signal, higher
    timeframe up. Exit: close < EMA50 or MACD crosses down. Stop 2.5 ATR, target 5 ATR,
    60 bars."""
    c, a = df["close"], ind.atr(df, 14)
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    m = ind.macd(c)
    entry = ((e20 > e50) & (e50 > e200) & (c > e20) & (ind.adx(df, 14) >= 25)
             & _cross_up(m["macd"], m["signal"]) & _htf_up(htf))
    exit_ = (c < e50) | _cross_up(m["signal"], m["macd"])
    return _out(df, entry, exit_, c - 2.5 * a, c + 5.0 * a, 60)


# ---------------------------------------------------------------------- B. momentum
def rsi_macd_roc_momentum(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """B. RSI crosses above 55, MACD histogram positive and rising, ROC(12) > 0, volume
    > 1.5x its 20-bar mean. Exit: RSI < 45. Stop 2 ATR, target 4 ATR, 30 bars."""
    c, a = df["close"], ind.atr(df, 14)
    r, h = ind.rsi(c, 14), ind.macd(c)["hist"]
    vol_ok = df["volume"] > 1.5 * df["volume"].shift(1).rolling(20, min_periods=20).mean()
    entry = _cross_up(r, 55.0) & (h > 0) & (h > h.shift(1)) & (ta.roc(c, 12) > 0) & vol_ok
    return _out(df, entry, r < 45, c - 2.0 * a, c + 4.0 * a, 30)


# ---------------------------------------------------------------------- C. breakout
def donchian_breakout(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """C1. Close above the previous 20-bar high with volume > 1.5x the 20-bar mean and the
    higher timeframe up. Exit: close below the previous 10-bar low (Turtle). Stop 2 ATR,
    target 6 ATR, 60 bars."""
    c, a = df["close"], ind.atr(df, 14)
    vol_ok = df["volume"] > 1.5 * df["volume"].shift(1).rolling(20, min_periods=20).mean()
    entry = (c > ind.donchian_high(df["high"], 20)) & vol_ok & _htf_up(htf)
    exit_ = c < ind.donchian_low(df["low"], 10)
    return _out(df, entry, exit_, c - 2.0 * a, c + 6.0 * a, 60)


def bollinger_squeeze_breakout(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """C2. Bollinger width (20, 2) in its lowest 20% of the last 120 bars within the
    previous 5 bars (a squeeze), then a close above the upper band with volume > 1.5x.
    Exit: close below the middle band. Stop 2 ATR, target 4 ATR, 40 bars."""
    c, a = df["close"], ind.atr(df, 14)
    bb = ind.bollinger(c, 20, 2.0)
    squeeze = ind.rolling_percentile(bb["width"], 120) <= 0.20
    recent = squeeze.astype(float).shift(1).rolling(5, min_periods=1).max() == 1
    vol_ok = df["volume"] > 1.5 * df["volume"].shift(1).rolling(20, min_periods=20).mean()
    entry = recent & (c > bb["upper"]) & vol_ok
    return _out(df, entry, c < bb["mid"], c - 2.0 * a, c + 4.0 * a, 40)


def atr_expansion_breakout(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """C3. Volatility expansion: a bullish bar whose body exceeds 1.5x the PREVIOUS ATR,
    closing in the top quarter of its range, above EMA50, volume > 2x the 20-bar mean.
    Exit: close below EMA20. Stop 0.5 ATR under the bar's low, target 3 ATR, 20 bars."""
    c, o = df["close"], df["open"]
    a_prev = ind.atr(df, 14).shift(1)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    vol_ok = df["volume"] > 2.0 * df["volume"].shift(1).rolling(20, min_periods=20).mean()
    entry = ((c - o) > 1.5 * a_prev) & ((c - df["low"]) / rng >= 0.75) \
        & (c > ind.ema(c, 50)) & vol_ok
    return _out(df, entry, c < ind.ema(c, 20), df["low"] - 0.5 * a_prev, c + 3.0 * a_prev, 20)


# ---------------------------------------------------------------- D. mean reversion
def bb_rsi_reversion(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """D1. Non-trending market (ADX < 20): the previous close below the lower Bollinger band
    with RSI < 30, then this bar closes back up (close > open and > previous close).
    Exit / target: the middle band. Stop 1.5 ATR, 20 bars."""
    c, a = df["close"], ind.atr(df, 14)
    bb = ind.bollinger(c, 20, 2.0)
    stretched = (c.shift(1) < bb["lower"].shift(1)) & (ind.rsi(c, 14).shift(1) < 30)
    entry = (ind.adx(df, 14) < 20) & stretched & (c > df["open"]) & (c > c.shift(1))
    return _out(df, entry, c >= bb["mid"], c - 1.5 * a, bb["mid"], 20)


def vwap_deviation_reversion(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """D2. Price stretched below a rolling 50-bar VWAP: z-score of (close - VWAP) < -2,
    RSI < 35, ADX < 25, and this bar closes above the previous close. Exit: z >= 0.
    Target: the VWAP. Stop 1.5 ATR, 20 bars."""
    c, a = df["close"], ind.atr(df, 14)
    tp = (df["high"] + df["low"] + c) / 3
    pv = (tp * df["volume"]).rolling(50, min_periods=50).sum()
    vwap = pv / df["volume"].rolling(50, min_periods=50).sum().replace(0, np.nan)
    dev = c - vwap
    z = dev / dev.rolling(50, min_periods=50).std()
    entry = (z < -2.0) & (ind.rsi(c, 14) < 35) & (ind.adx(df, 14) < 25) & (c > c.shift(1))
    return _out(df, entry, z >= 0, c - 1.5 * a, vwap, 20)


# ---------------------------------------------------- E. multi-timeframe structure
def mtf_structure_trend(df: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    """E. Market structure with higher-timeframe confirmation.
    * Entry: the higher timeframe is up, the last two CONFIRMED swing lows are rising (a
      higher low), and the close breaks above the last confirmed swing high (resistance).
      Swing points use 3 bars each side and are known 3 bars after they form.
    * Exit: close below the last swing low.
    * Stop: 0.25 ATR under the last swing low, at most 3 ATR away.
    * Target: 2R. Time barrier: 40 bars."""
    c, a, k = df["close"], ind.atr(df, 14), 3
    hi, lo = df["high"], df["low"]
    is_ph = hi == hi.rolling(2 * k + 1, center=True, min_periods=2 * k + 1).max()
    is_pl = lo == lo.rolling(2 * k + 1, center=True, min_periods=2 * k + 1).min()
    # Confirmed k bars later: shift the flags (and the levels) forward by k.
    last_ph = hi.where(is_ph).shift(k).ffill()
    pl_level = lo.where(is_pl).shift(k)
    last_pl = pl_level.ffill()
    prev_pl = pl_level.dropna().shift(1).reindex(df.index).ffill()
    higher_low = last_pl > prev_pl
    entry = _htf_up(htf) & higher_low & _cross_up(c, last_ph)
    stop = np.maximum(last_pl - 0.25 * a, c - 3.0 * a)
    return _out(df, entry, c < last_pl, stop, c + 2.0 * (c - stop), 40)


@dataclass(frozen=True)
class LibraryStrategy:
    code: str
    family: str
    fn: Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]
    source: str


LIBRARY: dict[str, LibraryStrategy] = {s.code: s for s in (
    LibraryStrategy("ema_adx_macd_trend", "trend", ema_adx_macd_trend,
                    "TradingView MACD Strategy + EMA/ADX trend filter"),
    LibraryStrategy("rsi_macd_roc_momentum", "momentum", rsi_macd_roc_momentum,
                    "TradingView RSI Strategy + Freqtrade sample (RSI/MACD) + ROC"),
    LibraryStrategy("donchian_breakout", "breakout", donchian_breakout,
                    "TradingView Channel Breakout / Turtle 20-10"),
    LibraryStrategy("bollinger_squeeze_breakout", "breakout", bollinger_squeeze_breakout,
                    "TradingView Bollinger Bands Strategy (squeeze variant)"),
    LibraryStrategy("atr_expansion_breakout", "breakout", atr_expansion_breakout,
                    "volatility-expansion bar (ATR), volume-confirmed"),
    LibraryStrategy("bb_rsi_reversion", "mean_reversion", bb_rsi_reversion,
                    "Freqtrade sample strategy (Bollinger + RSI)"),
    LibraryStrategy("vwap_deviation_reversion", "mean_reversion", vwap_deviation_reversion,
                    "VWAP z-score reversion"),
    LibraryStrategy("mtf_structure_trend", "structure", mtf_structure_trend,
                    "swing-structure break with higher-timeframe confirmation"),
)}


def signals(code: str, base: pd.DataFrame, base_tf: str, higher: pd.DataFrame
            ) -> pd.DataFrame:
    """Signals of one library strategy on ``base`` (closed bars of ``base_tf``) with the
    higher-timeframe frame ``higher`` (closed bars of HIGHER[base_tf])."""
    htf = htf_align(base, base_tf, htf_frame(higher), HIGHER[base_tf])
    return LIBRARY[code].fn(base, htf)
