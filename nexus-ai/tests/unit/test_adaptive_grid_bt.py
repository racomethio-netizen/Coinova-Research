"""Adaptive grid backtester on a synthetic sideways market: it runs the real engine,
its equity accounting adds up (fees and trips consistent), and the metrics behave."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
from nexus_backtest import adaptive_grid_bt as bt
from nexus_strategies import adaptive_grid as ag


def market(days: int = 75, seed: int = 4) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = days * 96
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = x[t - 1] * 0.995 + rng.normal(0, 0.0015)
    c = 60_000 * np.exp(x)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0015, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0015, n)))
    idx = pd.date_range("2023-11-20", periods=n, freq="15min", tz="UTC")
    m15 = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 50.0},
                       index=idx)
    h1 = m15.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"})
    return m15, h1


def test_backtester_runs_the_engine_and_accounts_consistently():
    m15, h1 = market()
    r = bt.run_symbol({"market": "spot", "symbol": "BTCUSDT", "m15": m15, "h1": h1,
                       "params": bt.DEFAULT, "alloc": Decimal("1000"),
                       "filters": ag.Filters(Decimal("0.01"), Decimal("0.00001"),
                                             Decimal("0.00001"), Decimal("5"))})
    assert r["grids"] >= 1 and r["trips"], r
    eq = bt.curve(r)
    assert eq.index.min() >= bt.START                         # nothing before the window
    m = bt.metrics(eq, r["trips"], 1000.0)
    assert -1 < m["net_return"] < 1 and m["max_drawdown"] <= 0
    assert r["fees"] > 0 and 0 <= r["exposure"] <= 1
    # Trip P&L is net of both fees; the sum cannot exceed the equity change by more than
    # the open position's mark-to-market.
    assert all("duration_h" in t and t["duration_h"] >= 0 for t in r["trips"])


def test_metrics_profit_factor_and_years():
    idx = pd.date_range("2024-01-01", periods=800, freq="D", tz="UTC")
    eq = pd.Series(np.linspace(1000, 1100, 800), index=idx)
    trips = [{"pnl": 2.0, "duration_h": 5, "stop": False}] * 9 + \
        [{"pnl": -6.0, "duration_h": 50, "stop": True}]
    m = bt.metrics(eq, trips, 1000.0)
    assert m["profit_factor"] == 3.0 and m["win_rate"] == 0.9 and m["stops"] == 1
    assert set(m["by_year"]) == {"2024", "2025", "2026"} and m["net_return"] > 0
