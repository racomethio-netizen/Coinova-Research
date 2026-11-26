"""Multi-asset trend harness: FRED parsing, carry, no look-ahead, the 1x gross cap,
weekly rebalancing with costs, the long-only index rule, and the gates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import multi_asset_trend as mt


def _write(d: Path, sid: str, s: pd.Series) -> None:
    pd.DataFrame({"observation_date": s.index.strftime("%Y-%m-%d"),
                  sid: [("." if np.isnan(v) else f"{v:.6f}") for v in s]}).to_csv(
        d / f"{sid}.csv", index=False)


@pytest.fixture
def fred(tmp_path: Path) -> Path:
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2002-04-01", "2008-12-31")
    n = len(days)
    for i, (sid, inv, rid) in enumerate(mt.FX.values()):
        drift = 0.0004 if i % 2 == 0 else -0.0004               # planted trends
        level = 1.2 * np.exp(np.cumsum(rng.normal(drift, 0.006, n)))
        _write(tmp_path, sid, pd.Series(1 / level if inv else level, index=days))
        months = pd.date_range("2002-04-01", "2008-12-01", freq="MS")
        _write(tmp_path, rid, pd.Series(np.full(len(months), 3.0), index=months))
    idx = 1000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    s = pd.Series(idx, index=days)
    s.iloc[10] = np.nan                                           # FRED "." holiday
    _write(tmp_path, mt.STOCK_SERIES, s)
    _write(tmp_path, mt.USD_RATE, pd.Series(np.full(n, 2.0), index=days))
    return tmp_path


def test_build_inverts_quotes_and_adds_carry(fred: Path):
    px, ex, klass = mt.build(fred)
    assert set(px.columns) == set(mt.FX) | {mt.STOCK}
    assert klass[mt.STOCK] == "stock" and klass["EUR"] == "fx"
    raw = mt.read_fred(fred, "DEXJPUS")
    assert px["JPY"].iloc[5] == pytest.approx(1 / raw.loc[px.index[5]])
    # Carry: foreign 3% vs USD 2% accrues 1% a year on top of the spot return.
    d = ex.index[20]
    prev = px.index[px.index.get_loc(d) - 1]
    spot = px.loc[d, "EUR"] / px.loc[prev, "EUR"] - 1
    assert ex.loc[d, "EUR"] == pytest.approx(spot + 0.01 * (d - prev).days / 365)


def test_targets_use_only_past_data_and_cap_gross_at_one(fred: Path):
    px, ex, _ = mt.build(fred)
    a = mt.targets_tsmom(px, ex)
    cut = ex.index[600]
    px2, ex2 = px.copy(), ex.copy()
    px2.loc[px2.index >= cut] *= 1.5
    ex2.loc[ex2.index >= cut] = ex2.loc[ex2.index >= cut] * -3
    b = mt.targets_tsmom(px2, ex2)
    pd.testing.assert_frame_equal(a.loc[:cut], b.loc[:cut])
    gross = a.abs().sum(axis=1)
    assert (gross <= 1 + 1e-9).all() and gross.iloc[-1] == pytest.approx(1.0)
    s = mt.targets_sma(px, ex)
    assert (s[mt.STOCK] >= 0).all()                              # the index is never short


def test_simulation_charges_costs_weekly_and_finds_planted_trends(fred: Path):
    px, ex, klass = mt.build(fred)
    tg = mt.targets_tsmom(px, ex)
    start, end = pd.Timestamp("2004-01-01"), pd.Timestamp("2009-01-01")
    base = mt.simulate(tg, ex, klass, start=start, end=end)
    free = mt.simulate(tg, ex, klass, cost_mult=0.0, start=start, end=end)
    assert base["costs"] > 0 and base["daily"].sum() < free["daily"].sum()
    assert base["daily"].sum() > 0                               # planted trends earn
    assert 0.9 <= base["avg_gross"] <= 1.0 + 1e-9


def test_report_applies_all_gates():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2004-01-01", "2026-10-30")
    def fake(mu: float) -> dict:
        d = pd.Series(rng.normal(mu, 0.004, len(idx)), index=idx)
        st = mt.stats(d)
        return {**st, "halves": {"a": mt.stats(d[:2000]), "b": mt.stats(d[2000:])},
                "stress_2x": mt.stats(d - 0.00002), "sum_without_best": float(d.sum()) - 0.01,
                "_daily": d}
    rep = mt.report({"tsmom_12m": fake(0.0006), "sma_200": fake(0.0)}, mt.stats(
        pd.Series(rng.normal(0, 0.01, 100), index=idx[:100])))
    g = rep["strategies"]["tsmom_12m"]["gates"]
    assert set(g) == {"net_positive", "sharpe_ge_0_5", "significant_holm", "drawdown_le_20pct",
                      "positive_both_halves", "positive_60pct_of_years",
                      "positive_at_2x_costs", "positive_without_best"}
    assert rep["strategies"]["tsmom_12m"]["passed"]
    assert not rep["strategies"]["sma_200"]["passed"]
