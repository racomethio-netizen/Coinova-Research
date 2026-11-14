"""Candle frames and look-ahead-safe multi-timeframe alignment."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def to_frame(candles: Sequence[Any]) -> pd.DataFrame:
    """Candle-like objects (open_time, open, high, low, close, volume) -> float frame."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC", name="open_time"))
    df = pd.DataFrame({
        "open": [float(c.open) for c in candles], "high": [float(c.high) for c in candles],
        "low": [float(c.low) for c in candles], "close": [float(c.close) for c in candles],
        "volume": [float(c.volume) for c in candles],
    }, index=pd.DatetimeIndex([c.open_time for c in candles], name="open_time"))
    if df.index.tz is None:
        raise ValueError("candle times must be timezone-aware")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("candles must be strictly increasing")
    return df


def align_higher(base: pd.DataFrame, base_tf: str, higher: pd.DataFrame, higher_tf: str,
                 prefix: str) -> pd.DataFrame:
    """Attach higher-timeframe columns to base rows using only CLOSED higher bars.

    A base bar is evaluated at its close, ``open_time + base_period``. A higher bar is
    known only after its close, ``open_time + higher_period``. Each base row receives
    the latest higher bar whose close <= the base bar's close.
    """
    if higher.empty:
        out = base.copy()
        for col in higher.columns:
            out[f"{prefix}{col}"] = float("nan")
        return out
    left = pd.DataFrame({"_t": base.index + pd.Timedelta(seconds=TF_SECONDS[base_tf])})
    right = higher.add_prefix(prefix).reset_index(drop=True)
    right["_t"] = higher.index + pd.Timedelta(seconds=TF_SECONDS[higher_tf])
    merged = pd.merge_asof(left, right, on="_t", direction="backward",
                           allow_exact_matches=True)
    merged.index = base.index
    return base.join(merged.drop(columns=["_t"]))
