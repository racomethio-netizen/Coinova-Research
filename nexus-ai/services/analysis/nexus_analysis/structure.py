"""Swing points and market structure (BOS / CHoCH).

Swings are fractal: bar ``i`` is a swing high if its high is the strict maximum of the
``left`` bars before it and the ``right`` bars after it. It is only *confirmed* at bar
``i + right``; the structure engine never looks at an unconfirmed swing.

Structure labels follow the usual definitions:

* Uptrend: each confirmed swing high exceeds the previous swing high (HH) and each swing
  low exceeds the previous low (HL). Downtrend is the mirror image.
* BOS (break of structure): a close beyond the last confirmed swing in the trend
  direction (continuation).
* CHoCH (change of character): the first close beyond the last opposing swing, i.e. a
  close below the last swing low while in an uptrend (or above the last swing high in a
  downtrend). A CHoCH flips the trend state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd


class Trend(StrEnum):
    UP = "up"
    DOWN = "down"
    UNDEFINED = "undefined"


@dataclass(frozen=True)
class StructureState:
    """Structure as known at a bar (all values causal)."""

    trend: Trend
    last_swing_high: float | None
    last_swing_low: float | None
    event: str | None          # "bos_up", "bos_down", "choch_up", "choch_down" or None


def swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Confirmed fractal swings.

    Returns a frame indexed by the CONFIRMATION bar with columns ``kind`` ("high"/"low"),
    ``price`` and ``swing_time`` (the bar where the extreme formed).
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    high, low = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n = len(df)
    rows = []
    for i in range(left, n - right):
        window_h = high[i - left : i + right + 1]
        window_l = low[i - left : i + right + 1]
        is_high = high[i] == window_h.max() and (window_h == high[i]).sum() == 1
        is_low = low[i] == window_l.min() and (window_l == low[i]).sum() == 1
        confirm_idx = i + right
        if is_high:
            rows.append((confirm_idx, "high", high[i], df.index[i]))
        if is_low:
            rows.append((confirm_idx, "low", low[i], df.index[i]))
    if not rows:
        return pd.DataFrame(columns=["kind", "price", "swing_time"],
                            index=pd.DatetimeIndex([], tz=df.index.tz))
    rows.sort(key=lambda r: (r[0], r[1]))
    out = pd.DataFrame(
        {"kind": [r[1] for r in rows], "price": [r[2] for r in rows],
         "swing_time": [r[3] for r in rows]},
        index=pd.DatetimeIndex([df.index[r[0]] for r in rows], name="confirmed_at"),
    )
    return out


def market_structure(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Per-bar structure state (causal). Columns: trend, last_swing_high, last_swing_low,
    event. Events are reported on the bar whose CLOSE triggers them."""
    sw = swings(df, left, right)
    n = len(df)
    trend = np.full(n, Trend.UNDEFINED.value, dtype=object)
    last_high = np.full(n, np.nan)
    last_low = np.full(n, np.nan)
    event = np.full(n, None, dtype=object)
    close = df["close"].to_numpy(float)

    # Pending confirmed swings, applied as soon as they are confirmed.
    confirmed_by_bar: dict[int, list[tuple[str, float]]] = {}
    pos = {ts: i for i, ts in enumerate(df.index)}
    for ts, row in sw.iterrows():
        confirmed_by_bar.setdefault(pos[ts], []).append((row["kind"], float(row["price"])))

    cur_trend = Trend.UNDEFINED
    hi: float | None = None
    lo: float | None = None
    prev_hi: float | None = None
    prev_lo: float | None = None
    for i in range(n):
        for kind, price in confirmed_by_bar.get(i, []):
            if kind == "high":
                prev_hi, hi = hi, price
            else:
                prev_lo, lo = lo, price
        ev = None
        c = close[i]
        # Trend re-evaluation from the two most recent confirmed swings of each kind.
        if cur_trend is Trend.UP and lo is not None and c < lo:
            cur_trend, ev = Trend.DOWN, "choch_down"
        elif cur_trend is Trend.DOWN and hi is not None and c > hi:
            cur_trend, ev = Trend.UP, "choch_up"
        elif cur_trend is Trend.UP and hi is not None and c > hi:
            ev = "bos_up"
        elif cur_trend is Trend.DOWN and lo is not None and c < lo:
            ev = "bos_down"
        elif cur_trend is Trend.UNDEFINED and hi is not None and lo is not None \
                and prev_hi is not None and prev_lo is not None:
            if hi > prev_hi and lo > prev_lo:
                cur_trend = Trend.UP
            elif hi < prev_hi and lo < prev_lo:
                cur_trend = Trend.DOWN
        trend[i] = cur_trend.value
        last_high[i] = np.nan if hi is None else hi
        last_low[i] = np.nan if lo is None else lo
        event[i] = ev
    return pd.DataFrame({"trend": trend, "last_swing_high": last_high,
                         "last_swing_low": last_low, "event": event}, index=df.index)
