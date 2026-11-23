"""Statistical arbitrage, liquidity planning, market-condition labels, registry additions.

The Engle-Granger reference values below were produced with statsmodels 0.14.4
(``statsmodels.tsa.stattools.coint(log a, log b, autolag="aic")``) on the same seeded
series, in a separate environment (statsmodels is not a NEXUS dependency)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from nexus_exchange.models import OrderBook, Side
from nexus_execution.liquidity import ExecutionLimits, estimate, plan
from nexus_strategies import stat_arb

D = Decimal


def _pair(k: int) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(7)
    out = None
    for i in range(k + 1):                       # same draw order as the reference run
        n = 500 if i % 2 else 300
        b = np.cumsum(rng.normal(0, 0.02, n)) + 5
        e = np.zeros(n)
        phi = [0.8, 1.0, 0.95, 0.5, 0.99, 0.9][i]
        for t in range(1, n):
            e[t] = phi * e[t - 1] + rng.normal(0, 0.01)
        out = (pd.Series(np.exp(1.3 * b + 0.2 + e)), pd.Series(np.exp(b)))
    assert out is not None
    return out


# statsmodels coint t-statistics and 5% critical values for the six seeded pairs.
REFERENCE = {0: (-6.300458, -3.35664), 1: (-1.358042, -3.34840), 2: (-2.962728, -3.35664),
             3: (-13.20336, -3.34840), 4: (-1.928991, -3.35664), 5: (-5.386677, -3.34840)}


@pytest.mark.parametrize("k", sorted(REFERENCE))
def test_engle_granger_matches_statsmodels(k):
    a, b = _pair(k)
    r = stat_arb.engle_granger(a, b)
    stat, cv5 = REFERENCE[k]
    assert r.adf_stat == pytest.approx(stat, abs=1e-5)
    assert r.critical_values["5%"] == pytest.approx(cv5, abs=1e-4)
    assert r.cointegrated is (stat < cv5)
    if r.cointegrated:                       # a spurious regression's beta means nothing
        assert r.beta == pytest.approx(1.3, abs=0.1)


def test_half_life_of_known_ar1():
    rng = np.random.default_rng(1)
    s = np.zeros(5000)
    for t in range(1, len(s)):
        s[t] = 0.9 * s[t - 1] + rng.normal()
    hl = stat_arb.half_life(s)
    assert hl == pytest.approx(np.log(0.5) / np.log(0.9), rel=0.15)   # ~6.6 bars
    assert stat_arb.half_life(1.05 ** np.arange(60)) is None          # explosive
    assert stat_arb.half_life(np.arange(50.0)) is None                # pure trend


def test_select_pairs_ranks_cointegrated_first_and_explains_rejections():
    a, b = _pair(0)                  # cointegrated
    rng = np.random.default_rng(3)
    c = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.02, len(a))) + 5))   # independent walk
    rows = stat_arb.select_pairs(pd.DataFrame({"A": a, "B": b, "C": c}),
                                 stat_arb.PairConfig(min_correlation=0.3))
    assert rows[0]["pair"] == "A/B" and rows[0]["eligible"]
    rejected = [r for r in rows if not r["eligible"]]
    assert rejected and all(r["reasons"] for r in rejected)


def test_pair_signal_entry_exit_stop_and_cost_gate():
    a, b = _pair(0)
    co = stat_arb.engle_granger(a.iloc[:240], b.iloc[:240])          # formation window only
    cfg = stat_arb.PairConfig(entry_z=2.0, z_window=60)
    s = stat_arb.spread_series(a, b, co.beta, co.intercept)
    win = s.iloc[-60:]
    # Push A's last close so the spread is ~3 sd rich -> SHORT_SPREAD.
    bump = float(3 * win.std(ddof=1) + (win.mean() - s.iloc[-1]))
    a2 = a.copy()
    a2.iloc[-1] = a.iloc[-1] * np.exp(bump)
    sig = stat_arb.pair_signal(a2, b, co, cfg=cfg)
    assert sig.decision == "SHORT_SPREAD" and sig.z > 2 and "cointegrated" in sig.reasons[0]
    # Same setup with prohibitive costs -> NO_TRADE, with the cost reason.
    costly = stat_arb.PairConfig(entry_z=2.0, z_window=60, fee_rate=0.02)
    no = stat_arb.pair_signal(a2, b, co, cfg=costly)
    assert no.decision == "NO_TRADE" and any("round-trip cost" in r for r in no.reasons)
    # In position: a huge move stops, a reverted spread exits.
    a3 = a.copy()
    a3.iloc[-1] = a.iloc[-1] * np.exp(float(6 * win.std(ddof=1) + (win.mean() - s.iloc[-1])))
    assert stat_arb.pair_signal(a3, b, co, in_position="SHORT_SPREAD").decision == "STOP"
    a4 = a.copy()
    a4.iloc[-1] = a.iloc[-1] * np.exp(float(win.mean() - s.iloc[-1]))
    assert stat_arb.pair_signal(a4, b, co, in_position="SHORT_SPREAD").decision == "EXIT"


def test_pair_signal_refuses_non_cointegrated_pair():
    a, b = _pair(1)                                                  # phi = 1: random walk
    co = stat_arb.engle_granger(a, b)
    sig = stat_arb.pair_signal(a, b, co)
    assert sig.decision == "NO_TRADE" and any("not cointegrated" in r for r in sig.reasons)


# ------------------------------------------------------------------ liquidity planning
def _book(spread_bps: float = 2, level_qty: str = "1", levels: int = 10) -> OrderBook:
    mid = D("100")
    half = mid * D(str(spread_bps)) / D("20000")
    step = D("0.02")
    bids = tuple((mid - half - step * i, D(level_qty)) for i in range(levels))
    asks = tuple((mid + half + step * i, D(level_qty)) for i in range(levels))
    return OrderBook("BTC/USDT", bids, asks, datetime(2026, 11, 23, tzinfo=UTC))


def test_estimate_walks_the_book():
    e = estimate(_book(), Side.BUY, D("2.5"))
    # 1 @ 100.01, 1 @ 100.03, 0.5 @ 100.05 -> avg 100.026
    assert e.fully_fillable and e.levels_used == 3
    assert e.avg_price == D("250.065") / D("2.5")
    assert e.slippage_vs_mid_bps == D("2.60") and e.spread_bps == D("2.00")
    short = estimate(_book(levels=2), Side.SELL, D("5"))
    assert not short.fully_fillable and short.filled == D("2")


def test_plan_ioc_capped_and_sliced():
    p = plan(_book(level_qty="1"), Side.BUY, D("3"), price_tick=D("0.01"),
             amount_step=D("0.001"), limits=ExecutionLimits(max_participation=D("0.25")))
    assert p.approved and p.action == "ioc_limit"
    assert len(p.child_quantities) == 2 and sum(p.child_quantities) == D("3")
    assert p.limit_price is not None and p.limit_price > D("100.05")      # capped, bounded


def test_plan_prefers_post_only_on_wide_spread_unless_urgent():
    wide = _book(spread_bps=12)
    assert plan(wide, Side.BUY, D("0.5"), price_tick=D("0.01")).action == "post_only"
    assert plan(wide, Side.BUY, D("0.5"), urgent=True).action == "ioc_limit"
    assert plan(wide, Side.BUY, D("0.5"), supports_post_only=False).action == "ioc_limit"
    po = plan(wide, Side.BUY, D("0.5"), price_tick=D("0.01"))
    assert po.limit_price == wide.best_bid                    # never crosses


def test_plan_rejects_illiquid_entries_but_never_blocks_exits():
    huge_spread = _book(spread_bps=60)
    rej = plan(huge_spread, Side.BUY, D("0.5"))
    assert not rej.approved and "spread" in rej.reasons[0]
    thin = _book(levels=2)
    assert not plan(thin, Side.BUY, D("5")).approved
    ex = plan(huge_spread, Side.SELL, D("0.5"), purpose="exit")
    assert ex.approved and ex.action == "ioc_limit" and ex.warnings
    too_big = plan(_book(level_qty="0.1"), Side.BUY, D("0.9"),
                   limits=ExecutionLimits(max_slices=2))
    assert not too_big.approved and "slices" in too_big.reasons[0]


# ------------------------------------------------------------------ market conditions
def test_market_condition_labels_are_causal_and_cover_regimes():
    from nexus_backtest.market_conditions import by_condition, fragile_conditions, label_days

    idx = pd.date_range("2020-01-01", periods=900, freq="D", tz="UTC")
    up = np.linspace(0, 1.2, 450)
    down = 1.2 - np.linspace(0, 1.5, 450)
    close = pd.Series(np.exp(np.r_[up, down]) * 10000, index=idx)
    close.iloc[600] = close.iloc[599] * 0.85                         # a crash day
    lab = label_days(close, pd.Series(1.0, index=idx))
    assert lab["trend"].iloc[400] == "bull" and lab["trend"].iloc[850] == "bear"
    assert bool(lab["crash"].iloc[605]) and not bool(lab["crash"].iloc[300])
    # Causal: labels up to day t do not change when the future changes.
    alt = close.copy()
    alt.iloc[700:] = alt.iloc[700:] * 3
    lab2 = label_days(alt, pd.Series(1.0, index=idx))
    pd.testing.assert_frame_equal(lab.iloc[:700], lab2.iloc[:700])
    # A long-only strategy: earns in the bull, loses in the bear.
    rets = close.pct_change().fillna(0)
    rep = by_condition(rets, lab)
    assert rep["bull"]["net_return"] > 0 > rep["bear"]["net_return"]
    assert "bear" in fragile_conditions(rep)


def test_registry_lists_kucoin_and_gate_as_detected_only():
    from nexus_exchange import registry

    for ex in ("kucoin", "gate"):
        assert ex in registry.EXCHANGES
        assert registry.NEXUS_SUPPORT[ex]["spot_adapter"] is False
        assert "detected only" in registry.NEXUS_SUPPORT[ex]["note"]


def test_random_forest_family_is_opt_in():
    from nexus_ai import candidates

    assert "rf" in candidates.FAMILIES and "rf" not in candidates.available_families()
    est = candidates.make_estimator("rf")
    X = np.random.default_rng(0).normal(size=(400, 3))
    y = (X[:, 0] > 0).astype(int)
    assert est.fit(X, y).score(X, y) > 0.9
