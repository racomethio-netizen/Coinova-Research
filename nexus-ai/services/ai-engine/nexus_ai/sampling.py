"""Information-driven sampling (López de Prado, Advances in Financial Machine Learning,
ch. 2):
* symmetric CUSUM event filter;
* volume bars, dollar bars and range bars built from finer OHLCV bars.

Causal by construction. An event at bar t uses data up to t only, and a bar is emitted when
the bar that completes its threshold has CLOSED. These tools sample from candles, not
trades: a threshold crossed inside a candle is detected at the candle's close (documented
approximation; NEXUS stores no tick history).

Triple-barrier labels are in ``nexus_ai.labels.triple_barrier``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cusum_events(close: pd.Series, threshold: float | pd.Series) -> pd.DatetimeIndex:
    """Times where the cumulative log-return drift since the last event exceeds
    ``threshold`` (a fraction, or a per-bar Series such as k x daily volatility)."""
    r = np.log(close).diff().dropna()
    h = threshold if isinstance(threshold, pd.Series) else pd.Series(threshold, index=r.index)
    h = h.reindex(r.index)
    s_pos = s_neg = 0.0
    events = []
    for t, x in r.items():
        lim = h.get(t)
        if lim is None or not np.isfinite(lim) or lim <= 0:
            continue
        s_pos, s_neg = max(0.0, s_pos + x), min(0.0, s_neg + x)
        if s_pos >= lim:
            s_pos = 0.0
            events.append(t)
        elif s_neg <= -lim:
            s_neg = 0.0
            events.append(t)
    return pd.DatetimeIndex(events)


def _aggregate(df: pd.DataFrame, group: np.ndarray) -> pd.DataFrame:
    g = df.groupby(group)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(),
                        "volume": g["volume"].sum(), "bars": g.size()})
    out.index = pd.DatetimeIndex(g.apply(lambda x: x.index[-1]), name="close_time")
    return out


def _threshold_groups(values: np.ndarray, threshold: float) -> tuple[np.ndarray, bool]:
    """Group id per row: a group closes on the row where the running sum reaches the
    threshold. Returns the ids and whether the last group is complete."""
    ids = np.empty(len(values), dtype=np.int64)
    acc, gid = 0.0, 0
    for i, v in enumerate(values):
        ids[i] = gid
        acc += v
        if acc >= threshold:
            acc, gid = 0.0, gid + 1
    return ids, acc == 0.0


def volume_bars(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """One bar per ``threshold`` of base volume (the last incomplete bar is dropped)."""
    ids, complete = _threshold_groups(df["volume"].to_numpy(float), threshold)
    out = _aggregate(df, ids)
    return out if complete else out.iloc[:-1]


def dollar_bars(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """One bar per ``threshold`` of quote value (close x volume per source bar)."""
    vals = (df["close"] * df["volume"]).to_numpy(float)
    ids, complete = _threshold_groups(vals, threshold)
    out = _aggregate(df, ids)
    return out if complete else out.iloc[:-1]


def range_bars(df: pd.DataFrame, width: float) -> pd.DataFrame:
    """A bar closes when the high-low range since its first source bar reaches ``width``
    (a fraction of the bar's opening price)."""
    ids = np.empty(len(df), dtype=np.int64)
    gid, lo, hi, base = 0, np.inf, -np.inf, None
    complete = True
    for i, (o, h, low) in enumerate(df[["open", "high", "low"]].to_numpy(float)):
        if base is None:
            base, lo, hi = o, np.inf, -np.inf
        ids[i] = gid
        lo, hi = min(lo, low), max(hi, h)
        complete = (hi - lo) / base >= width
        if complete:
            gid, base = gid + 1, None
    out = _aggregate(df, ids)
    return out if complete else out.iloc[:-1]
