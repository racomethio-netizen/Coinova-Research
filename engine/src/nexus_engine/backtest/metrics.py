"""Performance metrics (definitions fixed here and shown in the UI; docs/nexus/SIGNAL_ENGINE.md §6).

Statistics use floats; the money they summarize was computed in Decimal.
Outputs are strings so stored JSON is exact. Aggregates below the minimum
sample are flagged `insufficient_sample` and must not be headlined.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from .engine import Trade

MIN_SAMPLE = 100
BOOTSTRAP_RUNS = 1000


def _s(x: float | None, digits: int = 6) -> str | None:
    return None if x is None or not math.isfinite(x) else f"{x:.{digits}f}"


def max_drawdown(curve: Sequence[tuple[datetime, Decimal]]) -> float:
    peak = -math.inf
    worst = 0.0
    for _, eq in curve:
        e = float(eq)
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, (peak - e) / peak)
    return worst


def daily_sharpe(curve: Sequence[tuple[datetime, Decimal]], start: datetime, end: datetime) -> float | None:
    """Annualized Sharpe of daily returns of realized equity (365 days, risk-free 0)."""
    if not curve or end - start < timedelta(days=2):
        return None
    days: list[float] = []
    eq, j = float(curve[0][1]), 0
    d = start
    while d <= end:
        while j < len(curve) and curve[j][0] <= d:
            eq = float(curve[j][1])
            j += 1
        days.append(eq)
        d += timedelta(days=1)
    r = np.diff(np.array(days)) / np.array(days[:-1])
    if len(r) < 2 or float(np.std(r, ddof=1)) == 0.0:
        return None
    return float(np.mean(r) / np.std(r, ddof=1) * math.sqrt(365))


def _bootstrap(values: np.ndarray, stat: str, rng: np.random.Generator) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    idx = rng.integers(0, len(values), size=(BOOTSTRAP_RUNS, len(values)))
    samples = values[idx]
    stats = (samples > 0).mean(axis=1) if stat == "win_rate" else samples.mean(axis=1)
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def compute_metrics(
    trades: Sequence[Trade],
    curve: Sequence[tuple[datetime, Decimal]],
    initial_equity: Decimal,
    start: datetime,
    end: datetime,
    seed: int = 0,
    min_sample: int = MIN_SAMPLE,
) -> dict[str, object]:
    entered = [t for t in trades if t.entry_price is not None]
    pnl = np.array([float(t.pnl) for t in entered])
    rets = np.array([float(t.net_return or 0) for t in entered])
    rs = np.array([float(t.r_multiple or 0) for t in entered])
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    final = float(curve[-1][1]) if curve else float(initial_equity)
    rng = np.random.default_rng(seed)
    ci = {
        "win_rate": _bootstrap(pnl, "win_rate", rng),
        "expectancy_r": _bootstrap(rs, "mean", rng),
        "mean_net_return_per_trade": _bootstrap(rets, "mean", rng),
    }
    outcomes: dict[str, int] = {}
    for t in trades:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    n = len(entered)
    return {
        "signals": len(trades),
        "trades": n,
        "outcomes": outcomes,
        "insufficient_sample": n < min_sample,
        "min_sample": min_sample,
        "win_rate": _s(len(wins) / n) if n else None,
        "net_return": _s(final / float(initial_equity) - 1),
        "profit_factor": _s(float(wins.sum()) / abs(float(losses.sum()))) if len(losses) else None,
        "max_drawdown": _s(max_drawdown(curve)),
        "sharpe": {"value": _s(daily_sharpe(curve, start, end)), "basis": "daily realized equity, annualized x sqrt(365), risk-free 0"},
        "avg_win": _s(float(rets[pnl > 0].mean())) if len(wins) else None,
        "avg_loss": _s(float(rets[pnl < 0].mean())) if len(losses) else None,
        "expectancy_r": _s(float(rs.mean()), 4) if n else None,
        "confidence_intervals_95": {k: None if v is None else [_s(v[0]), _s(v[1])] for k, v in ci.items()},
        "bootstrap": {"runs": BOOTSTRAP_RUNS, "seed": seed},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
    }
