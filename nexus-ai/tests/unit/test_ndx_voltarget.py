"""Volatility-managed index holding: no look-ahead, the 10-point band, costs and cash."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import ndx_voltarget as nv


def walk(n: int = 1500, vol: float = 0.02, seed: int = 2) -> pd.Series:
    idx = pd.bdate_range("1995-01-02", periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(1000 * np.exp(np.cumsum(rng.normal(0.0003, vol, n))), index=idx)


def test_no_lookahead_and_costs():
    px = walk()
    a = nv.vol_target(px, 0.15)
    cut = px.index[900]
    px2 = px.copy()
    px2[px2.index > cut] *= 2
    b = nv.vol_target(px2, 0.15)
    pd.testing.assert_series_equal(a[a.index <= cut], b[b.index <= cut])
    free = nv.vol_target(px, 0.15, cost=0.0)
    assert (1 + free).prod() > (1 + a).prod()


def test_low_vol_is_fully_invested_and_cash_rate_accrues():
    px = walk(vol=0.003)                       # ~5% a year: weight capped at 1
    r = nv.vol_target(px, 0.15, cost=0.0)
    hold = px.pct_change()
    np.testing.assert_allclose(r.iloc[40:].to_numpy(), hold.iloc[40:].to_numpy(), rtol=1e-9)
    hi = walk(vol=0.04)                        # ~63%: mostly cash
    rate = pd.Series(0.05, index=hi.index)
    with_cash = nv.vol_target(hi, 0.15, cash_rate=rate)
    without = nv.vol_target(hi, 0.15)
    assert (1 + with_cash).prod() > (1 + without).prod()


def test_report_gates():
    px = walk(n=9500, seed=5)
    px.index = pd.bdate_range("1989-06-01", periods=len(px))
    rep = nv.report(px, pd.Series(0.03, index=px.index))
    assert set(rep["gates"]) == {"sharpe_above_hold", "drawdown_10pts_smaller", "net_positive",
                                 "sharpe_above_hold_both_halves", "holds_at_2x_costs"}
    assert rep["passed"] == all(rep["gates"].values())
    assert 0 < rep["avg_weight_target_15"] <= 1
    assert rep["target_15"]["sharpe"] == pytest.approx(rep["target_15"]["sharpe"])
