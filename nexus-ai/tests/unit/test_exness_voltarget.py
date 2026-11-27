"""Exness volatility control on synthetic daily bid/ask: the decision bar, fills at bid
and ask, the 10-point band, the 1x cap, financing and the 2x cost stress."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import exness_voltarget as ev


def frame(prices: np.ndarray, spread: float = 0.5, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(prices), tz="UTC")
    return pd.DataFrame({"bid": prices, "ask": prices + spread}, index=idx)


def test_daily_takes_the_last_bar_of_each_weekday(tmp_path):
    idx = pd.date_range("2024-01-05 23:45", periods=6, freq="12h", tz="UTC")  # Fri..Mon
    pd.DataFrame({"time": idx.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": 1.0, "high": 1.0,
                  "low": 1.0, "close": [10, 11, 12, 13, 14, 15],
                  "ask_close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5]}).to_csv(
        tmp_path / "X_M5.csv", index=False)
    d = ev.daily(tmp_path / "X_M5.csv")
    assert [t.day_name() for t in d.index] == ["Friday", "Monday"]   # weekend dropped
    assert d["bid"].tolist() == [10, 15] and d["ask"].tolist() == [10.5, 15.5]


def test_hold_buys_at_the_ask_and_marks_at_the_bid():
    d = frame(np.full(80, 100.0))
    h = ev.simulate(d, d.index[0], rule=False)
    units = 1.0 / (100.5 * (1 + ev.SLIP))
    assert h["equity"].iloc[-1] == pytest.approx(units * 100.0)


def test_rule_caps_at_1x_and_trades_only_beyond_the_band():
    rng = np.random.default_rng(0)
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, 200)))       # ~5% vol: weight 1
    led = ev.simulate(frame(calm, spread=0.0), frame(calm).index[40])
    assert led["weight"].max() <= 1.0 + 1e-9 and led["weight"].iloc[-1] > 0.9
    wild = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 200)))        # ~48% vol: ~0.3
    led = ev.simulate(frame(wild, spread=0.0), frame(wild).index[40])
    assert 0.15 < led["weight"].iloc[-1] < 0.5
    trades = int((led["units"].diff().abs() > 0).sum())
    assert 0 < trades < len(led) // 4                                # only beyond the band


def test_financing_and_cost_stress():
    d = frame(np.full(80, 100.0))
    rate = pd.Series([0.05], index=[d.index[0] - pd.Timedelta(days=10)])
    free = ev.simulate(d, d.index[0], rule=False)["equity"].iloc[-1]
    paid = ev.simulate(d, d.index[0], rule=False, rate=rate)["equity"].iloc[-1]
    days = (d.index[-1] - d.index[0]).days
    assert free - paid == pytest.approx(free * (0.05 + ev.MARKUP) * days / 365, rel=0.02)
    stressed = ev.simulate(d, d.index[0], rule=False, cost_mult=2.0)["equity"].iloc[-1]
    assert stressed == pytest.approx(100.0 / (101.0 * (1 + 2 * ev.SLIP)))
