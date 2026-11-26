"""FX styles harness on synthetic G10 data: carry in returns, monthly rebalancing from
past data only, 1x gross, top/bottom 3, the dollar carry sign, and the gates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import fx_styles as fx


def _write(d: Path, sid: str, s: pd.Series) -> None:
    pd.DataFrame({"observation_date": s.index.strftime("%Y-%m-%d"),
                  sid: [f"{v:.6f}" for v in s]}).to_csv(d / f"{sid}.csv", index=False)


@pytest.fixture
def fred(tmp_path: Path) -> Path:
    rng = np.random.default_rng(4)
    days = pd.bdate_range("1999-01-01", "2006-12-29")
    months = pd.date_range("1999-01-01", "2006-12-01", freq="MS")
    for i, (sid, inv, rid) in enumerate(fx.G10.values()):
        rate = 1.0 + i                                   # 1% ... 9%: a fixed carry ranking
        drift = 0.00002 * i                              # planted: high carry also rises
        level = 1.0 * np.exp(np.cumsum(rng.normal(drift, 0.004, len(days))))
        _write(tmp_path, sid, pd.Series(1 / level if inv else level, index=days))
        _write(tmp_path, rid, pd.Series(np.full(len(months), rate), index=months))
    _write(tmp_path, fx.USD_RATE, pd.Series(np.full(len(days), 3.0), index=days))
    return tmp_path


def test_carry_is_in_returns_and_targets_are_monthly_top_bottom_three(fred: Path):
    px, ex, diff = fx.build(fred)
    d = ex.index[100]
    prev = ex.index[99]
    eur_spot = px.loc[d, "EUR"] / px.loc[prev, "EUR"] - 1
    assert ex.loc[d, "EUR"] == pytest.approx(eur_spot + (0.01 - 0.03) * (d - prev).days / 365)
    tg = fx.targets(px, ex, diff)
    c = tg["carry"].dropna(how="all")
    row = c.iloc[-1]
    assert row.abs().sum() == pytest.approx(1.0)
    high = sorted(fx.G10, key=lambda k: list(fx.G10).index(k))[-3:]
    assert set(row[row > 0].index) == set(high)           # highest rates are bought
    assert all(t.day <= 7 for t in c.index)               # first trading day of a month
    d_row = tg["dollar_carry"].iloc[-1]                   # average foreign rate 5% > USD 3%
    assert (d_row > 0).all() and d_row.sum() == pytest.approx(1.0)
    assert tg["combined"].iloc[-1].abs().sum() == pytest.approx(1.0)


def test_no_lookahead(fred: Path):
    px, ex, diff = fx.build(fred)
    a = fx.targets(px, ex, diff)
    cut = ex.index[1500]
    px2, ex2 = px.copy(), ex.copy()
    px2.loc[px2.index >= cut] *= 2
    ex2.loc[ex2.index >= cut] *= -5
    b = fx.targets(px2, ex2, diff)
    for k in fx.STRATEGIES:
        pd.testing.assert_frame_equal(a[k].loc[:cut], b[k].loc[:cut])


def test_simulation_charges_costs_and_finds_planted_carry(fred: Path):
    px, ex, diff = fx.build(fred)
    tg = fx.targets(px, ex, diff)
    start, end = pd.Timestamp("2004-01-01"), pd.Timestamp("2007-01-01")
    base = fx.simulate(tg["carry"], ex, start=start, end=end)
    free = fx.simulate(tg["carry"], ex, cost_mult=0.0, start=start, end=end)
    assert base["daily"].sum() < free["daily"].sum()
    assert base["daily"].sum() > 0                        # rate gap + planted drift earn


def test_report_gates_and_holm():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2004-01-01", "2026-10-30")
    def fake(mu: float) -> dict:
        d = pd.Series(rng.normal(mu, 0.004, len(idx)), index=idx)
        st = fx.stats(d)
        return {**st, "halves": {"a": fx.stats(d[:2800]), "b": fx.stats(d[2800:])},
                "stress_2x": fx.stats(d - 0.00001), "sum_without_best": float(d.sum()) - 0.01,
                "_daily": d}
    rep = fx.report({k: fake(0.0006 if k == "carry" else 0.0) for k in fx.STRATEGIES})
    assert rep["strategies"]["carry"]["passed"]
    assert not rep["strategies"]["value"]["passed"]
    assert set(rep["correlations"]) == set(fx.STRATEGIES)
