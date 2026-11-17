"""Support/resistance from confirmed swings, and supply/demand zones.

Support/resistance: confirmed swing prices are clustered when they lie within
``tolerance_atr`` ATRs of each other. A level's strength is the number of swings in its
cluster. At each bar, the nearest level below (support) and above (resistance) are
reported using only swings confirmed by that bar.

Supply/demand zones (rally-base-drop style): an impulsive move of at least
``impulse_atr`` ATRs over ``impulse_bars`` bars is preceded by its base candle, the last
candle moving against the impulse. The zone is that base candle's range. A zone is
reported at the bar where the impulse is confirmed, and is marked mitigated once price
closes through it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from nexus_strategies.indicators import atr as atr_fn

from nexus_analysis.structure import swings


def support_resistance(df: pd.DataFrame, *, left: int = 2, right: int = 2,
                       tolerance_atr: float = 0.5, atr_len: int = 14,
                       lookback: int = 100) -> pd.DataFrame:
    """Per-bar nearest support/resistance from the ``lookback`` most recent confirmed swings,
    with strength counts (swings in the cluster)."""
    sw = swings(df, left, right)
    a = atr_fn(df, atr_len).to_numpy(float)
    close = df["close"].to_numpy(float)
    n = len(df)
    pos = {ts: i for i, ts in enumerate(df.index)}
    by_bar: dict[int, list[tuple[str, float]]] = {}
    for ts, row in sw.iterrows():
        by_bar.setdefault(pos[ts], []).append((row["kind"], float(row["price"])))

    sup = np.full(n, np.nan)
    res = np.full(n, np.nan)
    sup_n = np.zeros(n, dtype=int)
    res_n = np.zeros(n, dtype=int)
    prices: list[float] = []
    for i in range(n):
        prices.extend(p for _, p in by_bar.get(i, []))
        del prices[:-lookback]
        if not prices or np.isnan(a[i]) or a[i] <= 0:
            continue
        tol = tolerance_atr * a[i]
        levels: list[tuple[float, int]] = _cluster(sorted(prices), tol)
        below = [(p, k) for p, k in levels if p < close[i]]
        above = [(p, k) for p, k in levels if p > close[i]]
        if below:
            sup[i], sup_n[i] = max(below)[0], max(below)[1]
        if above:
            res[i], res_n[i] = min(above)[0], min(above)[1]
    return pd.DataFrame({"support": sup, "support_strength": sup_n,
                         "resistance": res, "resistance_strength": res_n}, index=df.index)


def _cluster(sorted_prices: list[float], tol: float) -> list[tuple[float, int]]:
    """Group prices so that no cluster spans more than ``tol`` (no chaining)."""
    clusters: list[list[float]] = []
    for p in sorted_prices:
        if clusters and p - clusters[-1][0] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(float(np.mean(c)), len(c)) for c in clusters]


def supply_demand_zones(df: pd.DataFrame, *, impulse_bars: int = 3, impulse_atr: float = 2.0,
                        atr_len: int = 14) -> pd.DataFrame:
    """Zones created by impulsive moves. Columns: kind ('demand' or 'supply'), low, high,
    base_time, created_at, mitigated_at (NaT while the zone is untouched)."""
    a = atr_fn(df, atr_len).to_numpy(float)
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = df.index
    zones: list[dict[str, object]] = []
    for i in range(impulse_bars, len(df)):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        start = i - impulse_bars
        move = c[i] - o[start + 1] if start + 1 <= i else 0.0
        if abs(move) < impulse_atr * a[i]:
            continue
        # Base: last candle before the impulse moving against it.
        base = None
        for j in range(start, max(-1, start - 5), -1):
            bearish = c[j] < o[j]
            if (move > 0 and bearish) or (move < 0 and not bearish):
                base = j
                break
        if base is None:
            continue
        kind = "demand" if move > 0 else "supply"
        zones.append({"kind": kind, "low": lo[base], "high": h[base],
                      "base_time": idx[base], "created_at": idx[i]})
    if not zones:
        return pd.DataFrame(columns=["kind", "low", "high", "base_time", "created_at",
                                     "mitigated_at"])
    out = pd.DataFrame(zones)
    mitigated = []
    for z in out.itertuples():
        after = (idx > z.created_at)
        m = None
        for k in np.flatnonzero(after):
            if (z.kind == "demand" and c[k] < z.low) or (z.kind == "supply" and c[k] > z.high):
                m = idx[k]
                break
        mitigated.append(m)
    out["mitigated_at"] = mitigated
    return out
