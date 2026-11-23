"""Ensemble Donchian trend following (Catching Crypto Trends): rules on synthetic data."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from nexus_backtest.crypto_trends import (
    SimConfig,
    annual_vol,
    calendar_years,
    combo_weights,
    donchian_position,
    max_drawdown,
    sharpe,
    simulate,
)

IDX = pd.date_range("2020-01-01", periods=12, freq="1D", tz="UTC")


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2020-01-01", periods=len(values),
                                                 freq="1D", tz="UTC"), dtype=float)


def test_entry_at_new_high_and_stop_from_midpoint() -> None:
    c = s([10, 11, 12, 13, 12.6, 12.4, 11])
    pos, stop = donchian_position(c, 3)
    # Day 2: close 12 = max(10,11,12) -> enter; stop = mid(10..12) = 11.
    assert list(pos[:3]) == [0, 0, 1] and stop.iloc[2] == 11
    # Day 3 (13): mid(11..13)=12 -> stop 12. Day 4 (12.6 > 12) held; stop max(12, 12)=12.
    assert stop.iloc[3] == 12 and pos.iloc[4] == 1
    # Day 5: 12.4 > stop max(12, mid(12.4..13)=12.7) = 12.7? close 12.4 <= 12.7 -> exit.
    assert pos.iloc[5] == 0


def test_stop_never_moves_down() -> None:
    rng = np.random.default_rng(0)
    c = s(list(100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, 400)))))
    pos, stop = donchian_position(c, 20)
    held = pos == 1
    # Within one holding period the stop is non-decreasing.
    run = (held != held.shift()).cumsum()
    for _, g in stop[held].groupby(run[held]):
        assert (g.diff().dropna() >= -1e-12).all()


def test_no_look_ahead() -> None:
    rng = np.random.default_rng(1)
    c = s(list(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 500)))))
    full = combo_weights(c, lookbacks=(5, 20, 60))
    part = combo_weights(c.iloc[:300], lookbacks=(5, 20, 60))
    pd.testing.assert_series_equal(full["weight"].iloc[:300], part["weight"])


def test_vol_sizing_and_cap() -> None:
    rng = np.random.default_rng(2)
    calm = s(list(100 * np.exp(np.cumsum(np.full(200, 0.01) + rng.normal(0, 0.001, 200)))))
    w = combo_weights(calm, lookbacks=(5,), cap=1.0)
    assert w["weight"].max() <= 1.0 + 1e-12            # spot: never leveraged
    v = annual_vol(calm).iloc[-1]
    assert v == pytest.approx(calm.pct_change().iloc[-90:].std() * math.sqrt(365))


def _bars(c: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "close": c})


def test_costs_and_next_open_execution() -> None:
    c = s([100.0] * 120 + [100 * 1.01 ** k for k in range(1, 61)])   # flat then trending
    free = simulate({"X": _bars(c)}, SimConfig(fee=0, slippage=0, lookbacks=(20,)))
    paid = simulate({"X": _bars(c)}, SimConfig(fee=0.001, slippage=0.0005, lookbacks=(20,)))
    assert free.trades > 0 and paid.costs > 0
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    # The first entry is filled the day AFTER the breakout close, never the same day.
    first_w = free.weights["X"].ne(0).idxmax()
    breakout = combo_weights(c, lookbacks=(20,))["weight"].ne(0).idxmax()
    assert first_w > breakout


def test_threshold_skips_small_vol_rebalances() -> None:
    rng = np.random.default_rng(3)
    c = s(list(100 * np.exp(np.cumsum(0.004 + rng.normal(0, 0.02, 600)))))
    tight = simulate({"X": _bars(c)}, SimConfig(threshold=0.0, lookbacks=(60,)))
    loose = simulate({"X": _bars(c)}, SimConfig(threshold=0.20, lookbacks=(60,)))
    assert loose.trades < tight.trades


def test_metrics() -> None:
    eq = s([100, 120, 90, 130])
    assert max_drawdown(eq) == pytest.approx(0.25)
    assert sharpe(eq) is None                              # too short to measure
    idx = pd.date_range("2021-01-01", "2022-12-31", freq="1D", tz="UTC")
    eq2 = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    years = calendar_years(eq2)
    assert set(years) == {2021, 2022} and all(v > 0 for v in years.values())
