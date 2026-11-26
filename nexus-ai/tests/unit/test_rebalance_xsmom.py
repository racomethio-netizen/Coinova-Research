"""Rebalancing and cross-sectional momentum backtests: no look-ahead, costs charged, and
a known synthetic case behaves as expected."""

from __future__ import annotations

import numpy as np
import pandas as pd
from nexus_backtest import rebalance_xsmom as rx


def closes(seed: int = 1, days: int = 1200) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-06-01", periods=days, freq="D", tz="UTC")
    return pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, days)))
                         for c in rx.COINS}, index=idx)


def test_rebalancing_mean_reverting_pair_beats_hold_and_pays_costs():
    idx = pd.date_range("2023-06-01", periods=1200, freq="D", tz="UTC")
    osc = 100 * np.exp(0.4 * np.sin(np.arange(1200) / 15))          # mean-reverting
    px = pd.DataFrame({c: 100.0 for c in rx.COINS}, index=idx)
    px["BTC"], px["ETH"] = osc, 100 * np.exp(-0.4 * np.sin(np.arange(1200) / 15))
    r = rx.rebalance(px, {"BTC": 0.5, "ETH": 0.5})
    assert r["rebalances"] > 10 and r["equity"].iloc[-1] > r["hold"].iloc[-1]
    free = rx.rebalance(px, {"BTC": 0.5, "ETH": 0.5}, cost=0.0)
    assert free["equity"].iloc[-1] > r["equity"].iloc[-1]          # costs are charged


def test_xsmom_has_no_lookahead_and_respects_filter():
    px = closes()
    a = rx.xsmom(px, 2, False)
    changed = px.copy()
    changed.loc[changed.index >= pd.Timestamp("2025-06-02", tz="UTC"), "DOGE"] *= 3
    b = rx.xsmom(changed, 2, False)
    cut = pd.Timestamp("2025-06-02", tz="UTC")
    pd.testing.assert_series_equal(a["returns"][a["returns"].index < cut],
                                   b["returns"][b["returns"].index < cut])
    crash = px.copy()
    crash["BTC"] = 100 * np.exp(-np.linspace(0, 3, len(px)))      # BTC always below its MA
    f = rx.xsmom(crash, 1, True)
    assert f["exposure"] < 0.05
