"""Daily time-series momentum (long/short) on perpetual futures.

Pre-registered in docs/research/2026-11-20-tsmom-preregistration.md. The position for day
t+1 is decided from closes up to day t and earns day t+1's close-to-close return (no
look-ahead). Costs are charged per side on every position change, and real funding is
applied (a long pays a positive rate; a short receives it).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from nexus_backtest.futures_data import BASE, month_range, parse_funding_zip, parse_klines_zip
from nexus_backtest.futures_validation import _cached

LOOKBACKS = (10, 20, 40, 60, 90)
COST_PER_SIDE = 0.0007  # 5 bps taker + 2 bps slippage


@dataclass(frozen=True)
class Gates:
    min_sharpe: float = 0.5
    min_positive_windows: float = 0.55
    min_t_stat: float = 1.0
    max_drawdown: float = 0.35


@dataclass
class Report:
    windows: list[dict[str, Any]]
    oos: pd.Series
    stats: dict[str, Any]
    passed: bool
    failures: list[str] = field(default_factory=list)


async def load_daily(symbol: str, start: str, end: str, cache_dir: Path) -> tuple[pd.Series, pd.Series]:
    """Daily closes (UTC open time) and daily funding sums for one perpetual."""
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    kl, fu = [], []
    async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
        for m in month_range(start, end):
            kl.append(parse_klines_zip(await _cached(
                client, f"{BASE}/klines/{symbol}/1d/{symbol}-1d-{m}.zip", cache_dir)))
            fu.append(parse_funding_zip(await _cached(
                client, f"{BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip", cache_dir)))
    k = pd.concat(kl).sort_index()
    close = k[~k.index.duplicated()]["close"].astype(float)
    f = pd.concat(fu).sort_index().astype(float)
    f = f[~f.index.duplicated()]
    # Funding settled during day d (00:00 <= t < next 00:00) is charged to day d's return.
    daily_funding = f.groupby(f.index.floor("D")).sum().reindex(close.index, fill_value=0.0)
    return close, daily_funding


def positions(close: pd.Series, lookback: int) -> pd.Series:
    """+1/-1 held DURING day t, decided from closes up to day t-1 (shifted once)."""
    signal = np.sign(close / close.shift(lookback) - 1.0)
    return signal.replace(0.0, np.nan).ffill().shift(1)


def daily_returns(close: pd.Series, funding: pd.Series, lookback: int) -> pd.Series:
    pos = positions(close, lookback)
    ret = close.pct_change()
    changes = pos.diff().abs().fillna(pos.abs())  # 1 = open/close, 2 = flip
    net = pos * ret - changes * COST_PER_SIDE - pos * funding
    return net.dropna()


def portfolio(data: dict[str, tuple[pd.Series, pd.Series]], lookback: int) -> pd.Series:
    parts = [daily_returns(c, f, lookback) for c, f in data.values()]
    return pd.concat(parts, axis=1).dropna().mean(axis=1)


def sharpe(r: pd.Series) -> float | None:
    if len(r) < 20 or r.std() == 0:
        return None
    return float(r.mean() / r.std() * math.sqrt(365))


def max_drawdown(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((1 - eq / eq.cummax()).max()) if len(eq) else 0.0


def walk_forward(data: dict[str, tuple[pd.Series, pd.Series]], *, train_days: int = 365,
                 test_days: int = 90, gates: Gates | None = None) -> Report:
    gates = gates or Gates()
    series = {lb: portfolio(data, lb) for lb in LOOKBACKS}
    idx = series[max(LOOKBACKS)].index
    windows: list[dict[str, Any]] = []
    parts: list[pd.Series] = []
    t = idx[0] + pd.Timedelta(days=train_days)
    while t + pd.Timedelta(days=test_days) <= idx[-1] + pd.Timedelta(days=1):
        lo, hi = t - pd.Timedelta(days=train_days), t + pd.Timedelta(days=test_days)
        scores = {lb: sharpe(s[(s.index >= lo) & (s.index < t)]) for lb, s in series.items()}
        valid = {lb: v for lb, v in scores.items() if v is not None}
        if valid:
            best = max(valid, key=lambda k: valid[k])
            test = series[best][(series[best].index >= t) & (series[best].index < hi)]
            parts.append(test)
            windows.append({"test_start": str(t.date()), "test_end": str(hi.date()),
                            "lookback": best, "train_sharpe": valid[best],
                            "test_return": float((1 + test).prod() - 1),
                            "test_sharpe": sharpe(test), "days": len(test)})
        t = hi
    oos = pd.concat(parts) if parts else pd.Series(dtype=float)
    n = len(oos)
    t_stat = float(oos.mean() / (oos.std() / math.sqrt(n))) if n > 1 and oos.std() > 0 else None
    positive = [w for w in windows if w["test_return"] > 0]
    stats: dict[str, Any] = {"days": n, "mean_daily": float(oos.mean()) if n else None, "sharpe": sharpe(oos),
             "total_return": float((1 + oos).prod() - 1) if n else None,
             "max_drawdown": max_drawdown(oos), "t_stat": t_stat,
             "positive_window_share": len(positive) / len(windows) if windows else None,
             "windows": len(windows)}
    failures = []
    if not n or stats["mean_daily"] is None or stats["mean_daily"] <= 0:
        failures.append(f"OOS mean daily return {stats['mean_daily']} <= 0")
    if stats["sharpe"] is None or stats["sharpe"] < gates.min_sharpe:
        failures.append(f"OOS Sharpe {stats['sharpe']} < {gates.min_sharpe}")
    if (stats["positive_window_share"] or 0) < gates.min_positive_windows:
        failures.append(f"positive windows {stats['positive_window_share']} < {gates.min_positive_windows}")
    if t_stat is None or t_stat < gates.min_t_stat:
        failures.append(f"t-stat {t_stat} < {gates.min_t_stat}")
    if float(stats["max_drawdown"]) > gates.max_drawdown:
        failures.append(f"max drawdown {stats['max_drawdown']} > {gates.max_drawdown}")
    return Report(windows, oos, stats, not failures, failures)


def buy_and_hold(data: dict[str, tuple[pd.Series, pd.Series]], days: pd.Index) -> dict[str, float]:
    parts = [(c.pct_change() - f).reindex(days) for c, f in data.values()]
    r = pd.concat(parts, axis=1).mean(axis=1).dropna()
    return {"total_return": float((1 + r).prod() - 1), "sharpe": sharpe(r) or 0.0,
            "max_drawdown": max_drawdown(r)}
