"""Support and resistance from confirmed swing points (docs/nexus/SIGNAL_ENGINE.md §2).

A swing high at bar p is *confirmed* only k bars later (bars p+1..p+k are all
lower), so it becomes known at index p+k. `levels_at(t)` uses only pivots
confirmed at or before t: no look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .indicators import Arr


@dataclass(frozen=True, slots=True)
class Level:
    price: float
    touches: int
    last_confirmed_index: int


def swing_points(high: Arr, low: Arr, k: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """(confirmation_index, price) for swing highs and swing lows."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for p in range(k, len(high) - k):
        window_h = high[p - k : p + k + 1]
        window_l = low[p - k : p + k + 1]
        if high[p] == window_h.max() and np.count_nonzero(window_h == high[p]) == 1:
            highs.append((p + k, float(high[p])))
        if low[p] == window_l.min() and np.count_nonzero(window_l == low[p]) == 1:
            lows.append((p + k, float(low[p])))
    return highs, lows


def _cluster(points: list[tuple[int, float]], tolerance: float) -> list[Level]:
    levels: list[Level] = []
    for idx, price in sorted(points, key=lambda x: x[1]):
        if levels and abs(price - levels[-1].price) <= tolerance:
            last = levels[-1]
            n = last.touches + 1
            levels[-1] = Level((last.price * last.touches + price) / n, n, max(last.last_confirmed_index, idx))
        else:
            levels.append(Level(price, 1, idx))
    return levels


def levels_at(high: Arr, low: Arr, t: int, atr_t: float, k: int = 3, cluster_atr: float = 0.5) -> tuple[list[Level], list[Level]]:
    """Resistance and support levels known at index t (pivots confirmed <= t)."""
    highs, lows = swing_points(high[: t + 1], low[: t + 1], k)
    tol = max(atr_t * cluster_atr, 0.0)
    return _cluster(highs, tol), _cluster(lows, tol)


def nearest_above(levels: list[Level], price: float) -> Level | None:
    above = [lv for lv in levels if lv.price > price]
    return min(above, key=lambda lv: lv.price) if above else None


def nearest_below(levels: list[Level], price: float) -> Level | None:
    below = [lv for lv in levels if lv.price < price]
    return max(below, key=lambda lv: lv.price) if below else None
