"""Breakdowns of out-of-sample signal studies (walk-forward ``oos`` frames).

Each group reports the same edge statistics as the validation gates (count, win rate,
mean net return, profit factor, median), so a reader can see WHERE a strategy loses: by
regime, coin, hour of day, weekday or exit type. Groups are descriptive only: choosing
trading hours or regimes from these tables after the fact would be data snooping, and any
such idea must be pre-registered and tested on new data.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from nexus_backtest.signals import edge_stats

MIN_GROUP = 20   # smaller groups are shown but flagged as too small to read


def _groups(oos: pd.DataFrame, key: pd.Series) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k, g in oos.groupby(key):
        stats: dict[str, Any] = dict(edge_stats(g))
        stats["too_small"] = len(g) < MIN_GROUP
        out[str(k)] = stats
    return out


def breakdown(oos: pd.DataFrame) -> dict[str, Any]:
    """``oos`` rows: entry_time, ret, win, outcome, regime and (optionally) symbol."""
    if oos.empty:
        return {"n": 0}
    t = pd.to_datetime(oos["entry_time"], utc=True)
    out: dict[str, Any] = {"n": len(oos), "all": dict(edge_stats(oos))}
    out["by_regime"] = _groups(oos, oos["regime"].fillna("unknown"))
    if "symbol" in oos:
        out["by_symbol"] = _groups(oos, oos["symbol"])
    # Four-hour blocks keep groups large enough to read; hours are UTC.
    out["by_hour_block_utc"] = _groups(oos, (t.dt.hour // 4 * 4).map(
        lambda h: f"{h:02d}-{h + 4:02d}"))
    out["by_weekday"] = _groups(oos, t.dt.day_name())
    out["by_outcome"] = _groups(oos, oos["outcome"])
    return out
