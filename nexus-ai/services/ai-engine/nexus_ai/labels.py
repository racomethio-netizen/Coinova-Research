"""Triple-barrier labels for a long entered at the NEXT bar's open.

label = 1 if the target (entry + tp_mult*ATR) is reached before the stop
(entry - sl_mult*ATR) within ``horizon`` bars; ties within one bar count as a stop
(conservative). ``ret`` is the realized net return of that rule including costs, and
``end_idx`` the index of the bar where the outcome was decided (used for purging).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from nexus_strategies import indicators as ind


def triple_barrier(df: pd.DataFrame, *, tp_mult: float = 2.0, sl_mult: float = 1.0,
                   horizon: int = 48, fee_rate: float = 0.001,
                   slippage_rate: float = 0.0005) -> pd.DataFrame:
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = ind.atr(df, 14).to_numpy()
    n = len(df)
    label = np.full(n, np.nan)
    ret = np.full(n, np.nan)
    end_idx = np.full(n, -1, dtype=np.int64)
    for t in range(n - 1):
        if np.isnan(a[t]):
            continue
        i = t + 1
        entry = o[i]
        stop, target = entry - sl_mult * a[t], entry + tp_mult * a[t]
        last = min(n - 1, i + horizon - 1)
        if last - i + 1 < horizon:
            break  # not enough future data: leave unlabeled (never guess)
        outcome, px, j_end = 0.0, c[last], last
        for j in range(i, last + 1):
            if lo[j] <= stop:
                outcome, px, j_end = 0.0, min(stop, o[j]) if j > i else stop, j
                break
            if h[j] >= target:
                outcome, px, j_end = 1.0, max(target, o[j]) if j > i else target, j
                break
        label[t] = outcome
        e = entry * (1 + slippage_rate) * (1 + fee_rate)
        x = px * (1 - slippage_rate) * (1 - fee_rate)
        ret[t] = x / e - 1
        end_idx[t] = j_end
    return pd.DataFrame({"label": label, "ret": ret, "end_idx": end_idx}, index=df.index)
