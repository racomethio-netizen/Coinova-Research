"""Bot league helpers on synthetic daily closes: holding, the rebalancing band, and the
NEXUS bot starting in cash on the league's first day."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import bot_league as bl


def days(n: int, start: str = "2021-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="1D", tz="UTC")


def test_hold_and_rebalance():
    idx = days(60, "2021-02-01")
    btc = pd.Series(np.r_[[100.0] * 30, [130.0] * 30], index=idx)   # BTC +30%, ETH flat
    eth = pd.Series(100.0, index=idx)
    h = bl.hold(btc, eth)
    assert h.iloc[0] == pytest.approx(10_000 * (1 - bl.COST))
    assert h.iloc[-1] == pytest.approx(5_000 * (1 - bl.COST) * 1.3 + 5_000 * (1 - bl.COST))
    r = bl.rebalance(btc, eth)
    # After the jump BTC is 56.5% (> 55%): one rebalance to 50/50, paying 0.15% on it.
    traded = (h.iloc[-1] / 2) - 5_000 * (1 - bl.COST)
    assert r.iloc[-1] == pytest.approx(h.iloc[-1] - 2 * traded * bl.COST, rel=1e-6)


def test_nexus_bot_starts_in_cash_on_the_first_league_day():
    idx = days(120)
    rng = np.random.default_rng(1)
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.04, 120))), index=idx)
    eth = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.05, 120))), index=idx)
    eq = bl.nexus_vol(btc, eth)
    assert eq.index[0] == bl.START
    # Day one: cash buys into the basket, paying at most 0.15% of the account.
    assert bl.CAPITAL * (1 - bl.COST) <= eq.iloc[0] <= bl.CAPITAL


def test_measure_reports_the_nexus_fit():
    idx = days(400, "2021-02-01")
    eq = pd.Series(np.linspace(10_000, 12_000, 400), index=idx)
    eq.iloc[100] = 9_000                                         # a -10% drawdown
    m = bl.measure(eq)
    assert m["max_drawdown"] == pytest.approx(9_000 / eq.iloc[:100].max() - 1)
    assert m["nexus_fit"]["allocation_share_for_5pct_drawdown"] == pytest.approx(
        0.05 / abs(m["max_drawdown"]))
