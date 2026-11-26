"""Market-neutral statistical model: point-in-time universe, no look-ahead, the model
finds a planted signal, costs and funding are charged, and the gates are applied."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import statarb_ml as sm

START = pd.Timestamp("2021-01-01", tz="UTC")


def panel(days: int = 640, coins: int = 80, reversal: float = 0.3, seed: int = 3,
          late: int = 0) -> dict[str, pd.DataFrame]:
    """Coins whose next-day return partly reverses today's (a planted, learnable edge).
    ``late`` coins list only 30 days before the end (too young for the universe)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(START, periods=days, freq="D", tz="UTC")
    syms = ["BTCUSDT", "USDCUSDT"] + [f"C{i:02d}USDT" for i in range(coins)]
    r = np.zeros((days, len(syms)))
    noise = rng.normal(0, 0.03, (days, len(syms)))
    for t in range(1, days):
        r[t] = -reversal * r[t - 1] + noise[t]
    r[:, 1] = rng.normal(0, 0.0005, days)                  # a stablecoin
    close = pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=idx, columns=syms)
    for j in range(late):
        close.iloc[: days - 30, -1 - j] = np.nan
    qv = pd.DataFrame(rng.lognormal(15, 0.5, close.shape), index=idx, columns=syms)
    qv[close.isna()] = np.nan
    return {"close": close, "high": close * 1.01, "low": close * 0.99, "qvol": qv,
            "funding": pd.DataFrame(0.0, index=idx, columns=syms)}


def test_universe_is_point_in_time_and_excludes_stables_and_young_coins():
    p = panel(late=3)
    _, _, uni, _ = sm.build(p)
    assert uni.sum(axis=1).max() <= sm.TOP_N
    assert not uni["USDCUSDT"].any()
    assert not uni.iloc[: sm.MIN_BARS].to_numpy().any()          # needs 60 bars first
    for c in p["close"].columns[-3:]:
        assert not uni[c].any()                                  # listed too recently


def test_features_use_only_past_data():
    p = panel()
    f1, _, _, _ = sm.build(p)
    k = 400
    q = {n: v.copy() for n, v in p.items()}
    q["close"].iloc[k:] *= np.linspace(1, 2, len(q["close"]) - k)[:, None]
    f2, _, _, _ = sm.build(q)
    for name in sm.FEATURES:
        pd.testing.assert_frame_equal(f1[name].iloc[: k + 1], f2[name].iloc[: k + 1])


def test_ridge_finds_the_planted_reversal_and_earns_it():
    p = panel()
    feats, target, uni, ret = sm.build(p)
    df = sm.long_frame(feats, target, uni)
    test_start = START + pd.Timedelta(days=430)
    pred, imp = sm.walk_forward(df, "ridge", start=test_start.normalize(),
                                end=START + pd.Timedelta(days=640))
    assert imp["r1"] < 0                                        # learned: reversal
    res = sm.portfolio(pred, ret, p["funding"], cost=0.0)
    assert res["daily"].mean() > 0 and len(res["daily"]) > 150
    costly = sm.portfolio(pred, ret, p["funding"])
    assert costly["costs"] > 0 and costly["daily"].sum() < res["daily"].sum()


def test_funding_paid_by_longs_received_by_shorts():
    idx = pd.date_range(START, periods=3, freq="D", tz="UTC")
    cols = [f"C{i:02d}USDT" for i in range(20)]
    ret = pd.DataFrame(0.0, index=idx, columns=cols)
    fund = pd.DataFrame(0.001, index=idx, columns=cols)
    scores = pd.DataFrame(np.tile(np.arange(20.0), (3, 1)), index=idx, columns=cols).stack()
    scores.index.names = ["date", "symbol"]
    res = sm.portfolio(scores, ret, fund, cost=0.0)
    assert res["funding"] == pytest.approx(0.0)                # 10 long pay = 10 short get
    fund.iloc[:, 10:] = 0.002                                   # the top 10 (longs) pay more
    res = sm.portfolio(scores, ret, fund, cost=0.0)
    assert res["funding"] < 0


def test_report_applies_every_gate():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-01-01", periods=1700, freq="D", tz="UTC")
    def fake(mu: float) -> dict:
        d = pd.Series(rng.normal(mu, 0.01, len(idx)), index=idx)
        st = sm.stats(d)
        return {**st, "stress_2x": sm.stats(d - 0.0005), "sum_without_top3": d.sum() - 0.1,
                "_daily": d}
    rep = sm.report({"ridge": fake(0.002), "xgboost": fake(0.0)}, fake(0.0))
    assert set(rep["models"]["ridge"]["gates"]) == {
        "net_positive", "sharpe_ge_1", "significant_holm", "drawdown_le_25pct",
        "positive_4_of_5_years", "positive_at_2x_costs", "beats_reversal_baseline",
        "positive_without_top3"}
    assert rep["models"]["ridge"]["passed"]
    assert not rep["models"]["xgboost"]["passed"]


def test_xgboost_path_trains_walk_forward():
    p = panel(days=520)
    feats, target, uni, _ = sm.build(p)
    df = sm.long_frame(feats, target, uni)
    start = (START + pd.Timedelta(days=460)).normalize()
    pred, imp = sm.walk_forward(df, "xgboost", start=start, end=START + pd.Timedelta(days=520))
    assert len(pred) > 0 and max(imp.values()) > 0
    assert pred.index.get_level_values("date").min() >= start


def test_plain_terms_breakdown_adds_up():
    m = {"sum_daily_returns": -0.5, "costs": 0.8, "funding": -0.9}
    rep = sm.plain_terms({"models": {"ridge": dict(m)}, "baseline_reversal_1d": dict(m)})
    b = rep["models"]["ridge"]["breakdown"]
    assert b["price_moves"] == pytest.approx(1.2)
    assert b["price_moves"] + b["trading_costs"] + b["funding"] == pytest.approx(b["total"])
