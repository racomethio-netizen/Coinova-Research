"""TSMOM grid mechanics (no look-ahead, tranches, costs, funding, vol scaling) and the
information-driven sampling tools."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_ai.sampling import cusum_events, dollar_bars, range_bars, volume_bars
from nexus_backtest import tsmom_grid as g

IDX = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")


def panel(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    close = pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(IDX))))
                          for c in ("A", "B")}, index=IDX)
    return close, pd.DataFrame(0.0, index=IDX, columns=close.columns)


def test_signal_and_position_never_use_future_data() -> None:
    close, fund = panel()
    full = g.run_config(close, fund, 7, 5, "long_short", True)
    cut = g.run_config(close.iloc[:40], fund.iloc[:40], 7, 5, "long_short", True)
    pd.testing.assert_frame_equal(full.iloc[:40], cut, check_freq=False)


def test_tranches_average_and_shift() -> None:
    w = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0]}, index=IDX[:4])
    pos = g.held(w, 2)
    assert pos["A"].tolist() == [0.0, 1.0, 0.5, 0.0]      # formed day 0, held days 1-2


def test_directions_and_gross_exposure() -> None:
    sig = pd.DataFrame({"A": [1.0], "B": [-1.0], "C": [np.nan]}, index=IDX[:1])
    assert g.target_weights(sig, "long").iloc[0].tolist() == [0.5, 0.0, 0.0]
    assert g.target_weights(sig, "short").iloc[0].tolist() == [0.0, -0.5, 0.0]
    ls = g.target_weights(sig, "long_short").iloc[0]
    assert ls.abs().sum() == pytest.approx(1.0)


def test_costs_and_funding_signs() -> None:
    close = pd.DataFrame({"A": [100.0, 110.0, 110.0]}, index=IDX[:3])
    pos = pd.DataFrame({"A": [0.0, 1.0, 1.0]}, index=IDX[:3])
    fund = pd.DataFrame({"A": [0.0, 0.001, 0.001]}, index=IDX[:3])
    res = g.pnl(pos, close, fund, cost=0.001)
    assert res["gross"].iloc[1] == pytest.approx(0.10)
    assert res["costs"].iloc[1] == pytest.approx(0.001)      # entry turnover 1
    assert res["funding"].iloc[1] == pytest.approx(0.001)    # a long pays positive funding
    short = g.pnl(-pos, close, fund, cost=0.0)
    assert short["funding"].iloc[1] == pytest.approx(-0.001)  # a short receives it


def test_risk_scale_uses_only_past_returns_and_never_levers() -> None:
    raw = pd.Series(np.r_[np.full(40, 0.001), np.full(20, 0.08)], index=IDX)
    pos = pd.DataFrame({"A": 1.0}, index=IDX)
    scaled = g.risk_managed(pos, raw)
    assert scaled["A"].max() <= 1.0
    assert scaled["A"].iloc[40] == pytest.approx(1.0)        # calm past (to day 39): full size
    assert scaled["A"].iloc[41] < 1.0                         # reacts to day 40 only after it
    assert scaled["A"].iloc[-1] < 0.5                         # volatile past: cut


def test_pbo_low_for_a_persistent_edge() -> None:
    rng = np.random.default_rng(1)
    m = pd.DataFrame(rng.normal(0, 0.01, (400, 12)))
    m[0] += 0.004                       # one configuration has a real, persistent edge
    pbo = g.pbo_cscv(m)
    assert pbo is not None and pbo < 0.05
    pbo_noise = g.pbo_cscv(pd.DataFrame(rng.normal(0, 0.01, (400, 12))))
    assert pbo_noise is not None and 0 <= pbo_noise <= 1


def test_cusum_events_and_bars() -> None:
    close = pd.Series([100, 101, 102, 103, 100, 97, 98, 99, 104.0], index=IDX[:9])
    ev = cusum_events(close, 0.025)
    assert IDX[3] in ev and IDX[5] in ev and IDX[8] in ev
    assert cusum_events(close.iloc[:6], 0.025).equals(ev[ev <= IDX[5]])   # causal
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": [1, 2, 3, 1, 2, 3, 1, 2, 3.0]})
    vb = volume_bars(df, 6)
    assert len(vb) == 3 and vb["volume"].tolist() == [6, 6, 6]
    assert vb.index[0] == IDX[2]                       # stamped at the completing bar
    db = dollar_bars(df, 1000)
    assert (db["bars"] >= 1).all() and db["close"].iloc[0] == df["close"].iloc[db["bars"].iloc[0] - 1]
    rb = range_bars(df, 0.05)
    assert len(rb) >= 1 and ((rb["high"] - rb["low"]) / rb["open"] >= 0.05).all()
