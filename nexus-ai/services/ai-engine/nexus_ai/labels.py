"""Triple-barrier labels for a long entered at the NEXT bar's open.

label = 1 if the target (entry + tp_mult*ATR) is reached before the stop
(entry - sl_mult*ATR) within ``horizon`` bars; ties within one bar count as a stop
(conservative). ``outcome`` is 1 (target first), -1 (stop first) or 0 (time barrier).
Barriers scale with ATR, i.e. they are volatility-adjusted. ``ret`` is the realized net
return of that rule including costs, and ``end_idx`` the index of the bar where the
outcome was decided (used for purging).

Also here: forward-return targets at several horizons, direction classes with a neutral
band derived from trading costs, and future-volatility targets. Every label uses only
bars AFTER the decision bar; rows without enough future data stay unlabeled.
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
    outcome_col = np.full(n, np.nan)
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
        outcome, px, j_end, kind = 0.0, c[last], last, 0.0
        for j in range(i, last + 1):
            if lo[j] <= stop:
                outcome, px, j_end, kind = 0.0, min(stop, o[j]) if j > i else stop, j, -1.0
                break
            if h[j] >= target:
                outcome, px, j_end, kind = 1.0, max(target, o[j]) if j > i else target, j, 1.0
                break
        label[t] = outcome
        outcome_col[t] = kind
        e = entry * (1 + slippage_rate) * (1 + fee_rate)
        x = px * (1 - slippage_rate) * (1 - fee_rate)
        ret[t] = x / e - 1
        end_idx[t] = j_end
    return pd.DataFrame({"label": label, "outcome": outcome_col, "ret": ret,
                         "end_idx": end_idx}, index=df.index)


def forward_returns(df: pd.DataFrame, horizons: tuple[int, ...] = (4, 16, 48), *,
                    round_trip_cost: float = 0.003) -> pd.DataFrame:
    """Net log return of buying at the NEXT bar's open and selling at the close ``h`` bars
    later, minus the round-trip cost. NaN where the future is not available."""
    o, c = df["open"], df["close"]
    out = pd.DataFrame(index=df.index)
    for h in horizons:
        out[f"fwd_{h}"] = np.log(c.shift(-h) / o.shift(-1)) - round_trip_cost
    return out


def direction_labels(fwd: pd.Series, *, band: float) -> pd.Series:
    """1 up, -1 down, 0 neutral (|net move| within ``band``). ``fwd`` should already be
    net of costs for the long side; the band keeps cost-sized noise out of both classes."""
    out = pd.Series(np.where(fwd > band, 1.0, np.where(fwd < -band, -1.0, 0.0)),
                    index=fwd.index)
    return out.where(fwd.notna())


def volatility_targets(df: pd.DataFrame, horizon: int = 48, lookback: int = 96
                       ) -> pd.DataFrame:
    """Realized volatility of the next ``horizon`` bars and whether it expands (> 1.2x the
    trailing ``lookback``-bar volatility known at the decision bar)."""
    r = np.log(df["close"]).diff()
    future = r[::-1].rolling(horizon, min_periods=horizon).std()[::-1].shift(-1)
    past = r.rolling(lookback, min_periods=lookback).std()
    return pd.DataFrame({"future_vol": future,
                         "vol_expansion": (future > 1.2 * past).astype(float)
                         .where(future.notna() & past.notna())})
