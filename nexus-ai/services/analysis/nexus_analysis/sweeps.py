"""Liquidity sweeps: price takes out a confirmed swing (where stops rest) and closes back
inside it. A sweep of a low is a long-bias signal; a sweep of a high a short-bias one.

Only swings confirmed on or before the previous bar are used, so a sweep never relies on
information from the bar it is judged on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_analysis.structure import swings


def liquidity_sweeps(df: pd.DataFrame, *, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Columns: sweep_low, sweep_high (booleans) and the swept level (NaN when none)."""
    sw = swings(df, left, right)
    n = len(df)
    pos = {ts: i for i, ts in enumerate(df.index)}
    lows_at: dict[int, list[float]] = {}
    highs_at: dict[int, list[float]] = {}
    for ts, row in sw.iterrows():
        bucket = lows_at if row["kind"] == "low" else highs_at
        bucket.setdefault(pos[ts], []).append(float(row["price"]))
    lo, hi, c = (df[k].to_numpy(float) for k in ("low", "high", "close"))
    sweep_low = np.zeros(n, dtype=bool)
    sweep_high = np.zeros(n, dtype=bool)
    level_low = np.full(n, np.nan)
    level_high = np.full(n, np.nan)
    last_low: float | None = None
    last_high: float | None = None
    for i in range(n):
        # Judge bar i against swings confirmed STRICTLY before it.
        if last_low is not None and lo[i] < last_low and c[i] > last_low:
            sweep_low[i], level_low[i] = True, last_low
        if last_high is not None and hi[i] > last_high and c[i] < last_high:
            sweep_high[i], level_high[i] = True, last_high
        for p in lows_at.get(i, []):
            last_low = p
        for p in highs_at.get(i, []):
            last_high = p
    return pd.DataFrame({"sweep_low": sweep_low, "sweep_high": sweep_high,
                         "swept_low_level": level_low, "swept_high_level": level_high},
                        index=df.index)
