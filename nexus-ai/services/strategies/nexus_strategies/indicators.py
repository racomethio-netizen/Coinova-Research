"""Causal technical indicators on OHLCV DataFrames.

Input frames: DatetimeIndex (UTC open_time), float columns open/high/low/close/volume.
Every value at row t uses only rows <= t (no centered windows, no backfill), so a
signal computed at a bar's close never sees the future. Indicators are statistics, so
float64 is used here; order prices/quantities are recomputed in Decimal downstream.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                      (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False,
                                                          min_periods=n).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False,
                                                            min_periods=n).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd,
                         "width": (2 * k * sd) / mid})


def donchian_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the PREVIOUS n bars (excludes the current bar)."""
    return high.shift(1).rolling(n, min_periods=n).max()


def donchian_low(low: pd.Series, n: int) -> pd.Series:
    return low.shift(1).rolling(n, min_periods=n).min()


def volume_z(volume: pd.Series, n: int = 50) -> pd.Series:
    """Current volume vs the previous n bars."""
    prev = volume.shift(1)
    mean = prev.rolling(n, min_periods=n).mean()
    sd = prev.rolling(n, min_periods=n).std()
    return (volume - mean) / sd.replace(0, np.nan)


def rolling_percentile(s: pd.Series, n: int, min_periods: int | None = None) -> pd.Series:
    """Rank of the current value within the trailing window (0..1). The window is FIXED
    (never derived from dataset length) so results don't depend on future data."""
    return s.rolling(n, min_periods=min_periods or n).rank(pct=True)


def realized_vol(close: pd.Series, n: int = 48) -> pd.Series:
    return np.log(close).diff().rolling(n, min_periods=n).std()
