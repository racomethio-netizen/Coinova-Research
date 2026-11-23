"""Phase G1: size-dependent slippage, extra report fields, parameter stability."""

from __future__ import annotations

from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest
from nexus_backtest.engine import BacktestConfig, SlippageModel, liquidity_inputs
from nexus_backtest.metrics import compute_metrics
from nexus_backtest.reports import breakdown
from nexus_backtest.stability import analyse, neighbours, summarise

from tests.unit.test_backtest import FLAT, run

# ------------------------------------------------------------------ slippage model


def test_square_root_impact() -> None:
    m = SlippageModel(base_rate=0.0005, impact_coef=1.0, max_rate=0.02)
    # 1% of daily volume, 3% daily volatility: impact 0.03 x 0.1 = 30 bps.
    assert m.rate(1e7, 0.03, 1e9) == pytest.approx(0.0005 + 0.003)
    # Four times the size: twice the impact (square root).
    assert m.rate(4e7, 0.03, 1e9) == pytest.approx(0.0005 + 0.006)
    assert m.rate(1e12, 0.03, 1e9) == 0.02                 # capped
    assert m.rate(1000, 0.0, 0.0) == 0.02                  # no history: never free


def _bars(days: int, volume: float) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=days * 288, freq="5min", tz="UTC")
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, len(idx))))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": volume}, index=idx)


def test_liquidity_inputs_use_only_finished_days() -> None:
    b = _bars(5, 1.0)
    liq = liquidity_inputs(b, lookback_days=2)
    day = pd.Timestamp("2026-01-04", tz="UTC")
    # Figures for bars on 4 Jan come from the two days that ENDED before it (2 and 3 Jan).
    daily_q = (b.volume * b.close).resample("1D").sum()
    assert liq.loc[day, "adv"] == pytest.approx(daily_q.iloc[1:3].mean())
    b2 = b.copy()
    b2.loc[day:, "volume"] = 1e9                           # change the future
    assert liquidity_inputs(b2, 2).loc[day, "adv"] == pytest.approx(liq.loc[day, "adv"])
    assert np.isnan(liq.loc[pd.Timestamp("2026-01-02", tz="UTC"), "adv"])  # not enough history


def test_engine_default_slippage_unchanged_and_cost_recorded() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    (t,) = run(rows, {0: ("entry", 95.0, 105.0)}, fee="0", slip="0.0005").trades
    # Entry 100 x 1.0005, exit 105 x 0.9995: slippage cost = both legs x quantity.
    assert t.slippage_cost == (D("100.05") - D(100) + D(105) - D("104.9475")) * t.quantity
    assert t.initial_risk == (t.entry_price - D(95)) * t.quantity


def test_engine_without_liquidity_history_pays_the_cap() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    cfg = BacktestConfig(fee_rate=D(0), slippage_rate=D(0),
                         slippage_model=SlippageModel(max_rate=0.01))
    (t,) = run(rows, {0: ("entry", 90.0, 120.0)}, cfg=cfg).trades
    assert t.entry_price == D("101.00")                    # 100 x (1 + 1% cap)


def test_metrics_new_fields() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    res = run(rows, {0: ("entry", 95.0, 105.0)}, fee="0.001", slip="0.0005")
    m = compute_metrics(res)
    (t,) = res.trades
    for key in ("recovery_factor", "max_drawdown_money", "avg_r", "slippage", "funding",
                "by_symbol", "by_side", "by_entry_hour_utc", "by_weekday"):
        assert key in m
    assert m["by_side"]["long"]["trades"] == 1 and m["funding"] == 0.0
    assert m["avg_r"] == pytest.approx(float(t.pnl / t.initial_risk))
    assert m["slippage"] == pytest.approx(float(t.slippage_cost))
    assert m["recovery_factor"] is None or m["recovery_factor"] > 0


# ------------------------------------------------------------------ OOS breakdown
def test_breakdown_groups_and_small_flag() -> None:
    t = pd.date_range("2026-01-05", periods=40, freq="1h", tz="UTC")
    oos = pd.DataFrame({"entry_time": t, "ret": np.where(np.arange(40) % 2, 0.01, -0.005),
                        "win": np.arange(40) % 2 == 1, "outcome": "stop",
                        "regime": ["range"] * 30 + ["trend_up"] * 10,
                        "symbol": "BTC/USDT"})
    b = breakdown(oos)
    assert b["n"] == 40 and b["by_regime"]["range"]["n"] == 30
    assert b["by_regime"]["trend_up"]["too_small"] is True
    assert set(b["by_hour_block_utc"]) == {"00-04", "04-08", "08-12", "12-16", "16-20", "20-24"}
    assert breakdown(pd.DataFrame()) == {"n": 0}


# ------------------------------------------------------------------ parameter stability
GRID = {"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0]}
COMBOS = [{"a": a, "b": b} for a in GRID["a"] for b in GRID["b"]]
T0 = pd.Timestamp("2026-01-01", tz="UTC")
WINDOWS = [(T0 + pd.Timedelta(days=60 * k), T0 + pd.Timedelta(days=60 * k + 40),
            T0 + pd.Timedelta(days=60 * k + 60)) for k in range(6)]


def _studies(quality: list[float], noise: float, seed: int) -> list[pd.DataFrame]:
    """One study per combo: trades every 12 h, mean return = quality + noise."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(T0, T0 + pd.Timedelta(days=360), freq="12h")
    out = []
    for q in quality:
        r = q + rng.normal(0, noise, len(t))
        out.append(pd.DataFrame({"entry_time": t, "exit_time": t + pd.Timedelta(hours=6),
                                 "ret": r}))
    return out


def test_neighbours_are_one_grid_step() -> None:
    i = COMBOS.index({"a": 2.0, "b": 10.0})
    got = {tuple(COMBOS[j].values()) for j in neighbours(COMBOS, i, GRID)}
    assert got == {(1.0, 10.0), (3.0, 10.0), (2.0, 20.0)}


def test_real_and_persistent_differences_are_stable() -> None:
    quality = [0.001 * k for k in range(len(COMBOS))]      # a true, persistent ranking
    rows = analyse(COMBOS, _studies(quality, 0.0005, 1), WINDOWS, GRID)
    s = summarise(rows, GRID)
    assert s["pbo"] == 0.0 and s["rank_corr_mean"] > 0.8
    assert s["most_chosen_share"] == 1.0


def test_pure_noise_is_flagged() -> None:
    rows = analyse(COMBOS, _studies([0.0] * len(COMBOS), 0.02, 2), WINDOWS, GRID)
    s = summarise(rows, GRID)
    assert s["rank_corr_mean"] is not None and abs(s["rank_corr_mean"]) < 0.6
    assert s["distinct_sets_chosen"] > 1                    # the choice keeps changing


def test_stability_oos_matches_walk_forward() -> None:
    """The stability report's OOS trades are exactly the walk-forward's."""
    from nexus_backtest.signals import CostModel
    from nexus_backtest.stability import parameter_stability
    from nexus_backtest.walkforward import walk_forward
    from nexus_strategies.library_v3 import V3_STRATEGIES

    rng = np.random.default_rng(3)
    idx = pd.date_range("2025-01-01", periods=300 * 288, freq="5min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    base = pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": rng.uniform(1, 3, len(idx))}, index=idx)
    frames = {tf: base.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                           "close": "last", "volume": "sum"})
              for tf, rule in (("5m", "5min"), ("15m", "15min"), ("1h", "1h"), ("4h", "4h"))}
    data = {"BTC/USDT": frames}
    cls = V3_STRATEGIES["vwap_trend"]   # trades often on random data
    costs = CostModel(0.001, 0.0005)
    rep = parameter_stability(data, cls, costs=costs, train_days=90, test_days=30)
    wf = walk_forward(data, cls, costs=costs, train_days=90, test_days=30)
    assert len(wf.oos) > 50 and len(rep.oos) == len(wf.oos)
    assert rep.oos.ret.sum() == pytest.approx(wf.oos.ret.sum())
