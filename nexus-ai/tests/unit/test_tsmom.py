from __future__ import annotations

import pandas as pd
import pytest
from nexus_backtest.tsmom import COST_PER_SIDE, daily_returns, positions


def _close(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC"))


def test_position_uses_only_past_closes() -> None:
    close = _close([100, 101, 102, 103, 104, 50])  # crash on the last day
    pos = positions(close, 2)
    # Day 5's position comes from closes up to day 4 (uptrend): still long on the crash day.
    assert pos.iloc[5] == 1.0
    # Changing the last close must not change any position up to and including that day.
    pos2 = positions(_close([100, 101, 102, 103, 104, 999]), 2)
    assert pos.equals(pos2)


def test_flip_costs_two_sides_and_funding_sign() -> None:
    close = _close([100, 101, 102, 101, 100, 99, 98])
    funding = pd.Series(0.001, index=close.index)
    r = daily_returns(close, funding, 1)
    pos = positions(close, 1)
    flip_day = pos.diff().abs().eq(2).idxmax()
    expected = pos[flip_day] * close.pct_change()[flip_day] - 2 * COST_PER_SIDE - pos[flip_day] * 0.001
    assert r[flip_day] == pytest.approx(expected)
    short_day = pos[pos == -1].index[-1]
    # A short RECEIVES positive funding.
    assert r[short_day] == pytest.approx(-close.pct_change()[short_day] + 0.001
                                         - pos.diff().abs()[short_day] * COST_PER_SIDE)
