"""Global strategy library (S4): causality, output contract, engine mechanics and the
statistics used by the pre-registered gates."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest.library_engine import Costs, metrics, run
from nexus_backtest.significance import holm, sqn, stationary_bootstrap_pvalue
from nexus_strategies.library_global import HIGHER, LIBRARY, resample, signals


def synthetic_15m(days: int = 400, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 96
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    # Regime-switching drift so that trend, breakout and reversion rules all fire.
    drift = np.repeat(rng.choice([-0.0004, 0.0, 0.0004], size=n // 960 + 1), 960)[:n]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.lognormal(8, 0.6, n)}, index=idx)


M15 = synthetic_15m()
FRAMES = {tf: resample(M15, tf) for tf in ("1h", "4h", "1d", "1w")}


@pytest.mark.parametrize("tf", ["1h", "4h"])
@pytest.mark.parametrize("code", list(LIBRARY))
def test_signals_are_causal(code: str, tf: str) -> None:
    """Freqtrade lookahead-analysis: signals computed on data cut at T equal the full-data
    signals up to T (the higher timeframe included)."""
    full = signals(code, FRAMES[tf], tf, FRAMES[HIGHER[tf]])
    for cut_day in (150, 260):
        cut = M15.index[0] + pd.Timedelta(days=cut_day)
        part15 = M15[M15.index < cut]
        part = signals(code, resample(part15, tf), tf, resample(part15, HIGHER[tf]))
        pd.testing.assert_frame_equal(full.loc[part.index], part, check_dtype=False,
                                      obj=f"{code} {tf} cut {cut_day}")


@pytest.mark.parametrize("code", list(LIBRARY))
def test_output_contract_and_levels(code: str) -> None:
    out = signals(code, FRAMES["1h"], "1h", FRAMES["4h"])
    assert list(out.columns) == ["entry", "exit", "stop", "target", "time_limit"]
    e = out[out.entry]
    close = FRAMES["1h"]["close"].loc[e.index]
    assert (e.stop < close).all() and (e.target > close).all()
    assert (out.time_limit > 0).all()


def test_every_strategy_fires_on_synthetic_data() -> None:
    fired = {c: int(signals(c, FRAMES["1h"], "1h", FRAMES["4h"]).entry.sum()) for c in LIBRARY}
    assert all(v > 0 for v in fired.values()), fired


def test_resample_drops_incomplete_last_bar() -> None:
    part = M15.iloc[:-3]                         # the last hour is incomplete
    assert resample(part, "1h").index[-1] == FRAMES["1h"].index[-2]


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]).assign(
        volume=1e9)


def _sig(df: pd.DataFrame, at: int, stop: float, target: float, exit_at: int | None = None,
         time_limit: int = 50) -> pd.DataFrame:
    s = pd.DataFrame({"entry": False, "exit": False, "stop": np.nan, "target": np.nan,
                      "time_limit": time_limit}, index=df.index)
    s.iloc[at, s.columns.get_loc("entry")] = True
    s.iloc[at, s.columns.get_loc("stop")] = stop
    s.iloc[at, s.columns.get_loc("target")] = target
    if exit_at is not None:
        s.iloc[exit_at, s.columns.get_loc("exit")] = True
    return s


FREE = Costs(taker_fee=0.0, maker_fee=0.0, half_spread_bps={}, default_half_spread_bps=0.0,
             slippage_bps=0.0)


def _run(df: pd.DataFrame, s: pd.DataFrame, costs: Costs = FREE) -> pd.DataFrame:
    return run({"X": s}, {"X": df}, "1h", start=df.index[0], end=df.index[-1] + pd.Timedelta(
        hours=1), costs=costs).trades


def test_next_open_fill_then_target_as_limit() -> None:
    df = _bars([(100, 100, 100, 100), (101, 101, 101, 101), (101, 106, 100.5, 105),
                (105, 105, 105, 105)])
    (t,) = _run(df, _sig(df, 0, 95, 104)).itertuples()
    assert t.entry == 101 and t.exit == 104 and t.exit_kind == "target"
    # 0.5% of 10,000 = 50 at risk over a 6.0 stop distance -> 8.333 units (5% vol cap free)
    assert t.qty == pytest.approx(50 / 6, rel=1e-3)


def test_stop_checked_before_target_and_gap_fills_at_open() -> None:
    both = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 110, 90, 100),
                  (100, 100, 100, 100)])
    (t,) = _run(both, _sig(both, 0, 95, 105)).itertuples()
    assert t.exit_kind == "stop" and t.exit == 95
    gap = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (90, 91, 89, 90),
                 (90, 90, 90, 90)])
    (g,) = _run(gap, _sig(gap, 0, 95, 105)).itertuples()
    assert g.exit == 90


def test_strategy_exit_and_time_barrier_fill_at_next_open() -> None:
    df = _bars([(100, 100, 100, 100)] * 2 + [(100, 101, 99.5, 100.5)] * 6
               + [(102, 102, 102, 102)] * 3)
    (t,) = _run(df, _sig(df, 0, 90, 120, exit_at=3)).itertuples()
    assert t.exit_kind == "strategy_exit" and t.exit_time == df.index[4]
    (u,) = _run(df, _sig(df, 0, 90, 120, time_limit=2)).itertuples()
    assert u.exit_kind == "time_limit" and u.bars == 2


def test_costs_reduce_pnl() -> None:
    df = _bars([(100, 100, 100, 100), (101, 101, 101, 101), (101, 106, 100.5, 105),
                (105, 105, 105, 105)])
    free = _run(df, _sig(df, 0, 95, 104)).pnl.iloc[0]
    paid = _run(df, _sig(df, 0, 95, 104), Costs()).pnl.iloc[0]
    assert paid < free


def test_metrics_shape() -> None:
    df = _bars([(100, 100, 100, 100), (101, 101, 101, 101), (101, 106, 100.5, 105),
                (105, 105, 105, 105)])
    res = run({"X": _sig(df, 0, 95, 104)}, {"X": df}, "1h", start=df.index[0],
              end=df.index[-1] + pd.Timedelta(hours=1), costs=FREE)
    m = metrics(res)
    assert m["trades"] == 1 and m["win_rate"] == 1.0 and m["profit_factor"] is None


def test_bootstrap_pvalue_separates_edge_from_noise() -> None:
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.02, 400)
    edge = rng.normal(0.006, 0.02, 400)
    p_noise = stationary_bootstrap_pvalue(noise - noise.mean(), simulations=1000)
    p_edge = stationary_bootstrap_pvalue(edge, simulations=1000)
    assert p_noise is not None and p_edge is not None
    assert p_edge < 0.01 < p_noise
    assert stationary_bootstrap_pvalue(np.ones(5)) is None


def test_sqn_and_holm() -> None:
    assert sqn(np.array([1.0, -1.0, 1.0, 1.0])) == pytest.approx(2 * 0.5 / 1.0)
    # Holm: 0.01 vs 0.05/3 passes; 0.03 vs 0.05/2 fails and stops the procedure.
    assert holm({"a": 0.01, "b": 0.03, "c": 0.04}) == {"a": True, "b": False, "c": False}
    assert holm({"a": None, "b": 0.001}) == {"a": False, "b": True}
