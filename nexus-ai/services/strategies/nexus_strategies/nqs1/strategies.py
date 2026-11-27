"""NQS-1 entry setups, vectorised. Each returns, per bar (known at that bar's close):
* ``dir``: +1 long, -1 short, 0 none;
* ``conf``: the number of independent confirmations that agree;
* ``stop_atr``: the initial stop distance in ATR;
* ``kind``: the target type ("r" for R multiples, "mid" for the Bollinger middle).

The engine then applies regime selection, at least two confirmations, the cost and
expected-value checks, and NEXUS risk.

Carry is not an entry signal. ``carry_per_day`` gives the daily cost or credit of
holding a position, which the engine charges and uses to refuse swing trades whose carry
would eat too much of the expected gain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGIES = ("tsmom", "trend", "breakout", "mean_reversion")


def _frame(f: pd.DataFrame, d: pd.Series, conf: pd.Series, stop: float, kind: str
           ) -> pd.DataFrame:
    return pd.DataFrame({"dir": d.fillna(0).astype(int), "conf": conf.fillna(0).astype(int),
                         "stop_atr": stop, "kind": kind}, index=f.index)


def tsmom(f: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Time-series momentum: the sign of the past ``lookback``-bar return, entered when
    it changes. Confirmations: the momentum sign itself, the MACD histogram, and the side
    of EMA 200."""
    ret = f["close"] / f["close"].shift(lookback) - 1
    sign = np.sign(ret)
    flip = sign.ne(sign.shift(1)) & sign.ne(0)
    d = sign.where(flip, 0)
    conf = (1 + (np.sign(f["macd_hist"]) == sign).astype(int)
            + (np.sign(f["close"] - f["ema200"]) == sign).astype(int))
    return _frame(f, d, conf.where(d != 0, 0), 2.0, "r")


def trend(f: pd.DataFrame) -> pd.DataFrame:
    """Multi-timeframe trend following: the higher-timeframe trend (EMA 50 vs 200,
    ADX >= 20) and a pullback to EMA 20 on the signal timeframe that resumes.
    Confirmations: higher-timeframe trend, signal-timeframe EMA 50/200, RSI back through
    50, MACD histogram rising."""
    htf = np.where((f["htf_ema50"] > f["htf_ema200"]) & (f["htf_adx"] >= 20), 1,
                   np.where((f["htf_ema50"] < f["htf_ema200"]) & (f["htf_adx"] >= 20), -1, 0))
    htf_s = pd.Series(htf, index=f.index)
    near = (f["low"].rolling(3).min() <= f["ema20"] + 0.5 * f["atr"]) & \
        (f["high"].rolling(3).max() >= f["ema20"] - 0.5 * f["atr"])  # pulled back to EMA 20
    long_ = (htf_s == 1) & near & (f["close"] > f["ema20"]) & (f["rsi"] > 50) & \
        (f["rsi"].shift(1) <= 50)
    short = (htf_s == -1) & near & (f["close"] < f["ema20"]) & (f["rsi"] < 50) & \
        (f["rsi"].shift(1) >= 50)
    d = pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=f.index)
    conf = (1 + (np.sign(f["ema50"] - f["ema200"]) == d).astype(int)
            + (np.sign(f["macd_hist"] - f["macd_hist"].shift(1)) == d).astype(int)
            + (np.sign(f["rsi"] - 50) == d).astype(int))
    return _frame(f, d, conf.where(d != 0, 0), 1.5, "r")


def breakout(f: pd.DataFrame, squeeze: float = 0.15) -> pd.DataFrame:
    """Volatility-adjusted breakout: a close beyond the 20-bar Donchian channel by at
    least 0.25 ATR, after a recent squeeze. Confirmations: the breakout, MACD above its
    signal line, ADX rising, momentum agreeing."""
    up = f["close"] > f["dc_high"] + 0.25 * f["atr"]
    dn = f["close"] < f["dc_low"] - 0.25 * f["atr"]
    recent_squeeze = f["width_pct"].rolling(10).min() <= 2 * squeeze
    d = pd.Series(np.where(up & recent_squeeze, 1, np.where(dn & recent_squeeze, -1, 0)),
                  index=f.index)
    conf = (1 + (np.sign(f["macd"] - f["macd_signal"]) == d).astype(int)
            + (f["adx"] > f["adx"].shift(3)).astype(int)
            + (np.sign(f["mom"]) == d).astype(int))
    return _frame(f, d, conf.where(d != 0, 0), 1.5, "r")


def mean_reversion(f: pd.DataFrame) -> pd.DataFrame:
    """Mean reversion inside a strict range: ADX < 18, a Donchian channel no wider than
    4 ATR, a close outside the Bollinger band and RSI stretched (< 30 or > 70). Target:
    the Bollinger middle. Stop: 1 ATR beyond the channel."""
    narrow = (f["dc_high"] - f["dc_low"]) <= 4 * f["atr"]
    ranging = (f["adx"] < 18) & narrow
    long_ = ranging & (f["close"] < f["bb_low"]) & (f["rsi"] < 30)
    short = ranging & (f["close"] > f["bb_up"]) & (f["rsi"] > 70)
    d = pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=f.index)
    conf = (1 + ranging.astype(int)
            + (np.sign(f["macd_hist"] - f["macd_hist"].shift(1)) == d).astype(int))
    return _frame(f, d, conf.where(d != 0, 0), 1.0, "mid")


def carry_per_day(rate_base: float, rate_quote: float, direction: int,
                  markup: float = 0.005) -> float:
    """Daily carry as a fraction of notional for holding ``direction`` (long = buy the
    base currency). A broker markup is charged on both sides; real swaps come from the
    Exness bridge."""
    return (direction * (rate_base - rate_quote) - markup) / 365
