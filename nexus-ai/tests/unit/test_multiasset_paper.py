"""Multi-asset research worker: descriptive analysis, the NASDAQ-100 paper ledger from its
start date, and the ledger matching the tested rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import multiasset_paper as mp
from nexus_backtest import ndx_voltarget as nv


def series(n: int = 700, start: str = "2025-01-02", vol: float = 0.012) -> pd.Series:
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(3)
    return pd.Series(20_000 * np.exp(np.cumsum(rng.normal(0.0003, vol, n))), index=idx)


def test_analyse_describes_without_predicting():
    s = series()
    a = mp.analyse(s, ann=252)
    assert a["last"] == pytest.approx(s.iloc[-1]) and a["as_of"] == str(s.index[-1].date())
    assert a["return_12m"] == pytest.approx(s.iloc[-1] / s.iloc[-253] - 1)
    assert isinstance(a["above_200d"], bool) and a["vol_30d"] > 0
    short = mp.analyse(s.iloc[:50], ann=252)
    assert short["return_12m"] is None and short["above_200d"] is None
    assert mp.analyse(s.iloc[:1], ann=252) == {"error": "not enough data"}


def test_ledger_matches_the_tested_rule():
    s = series(1500, start="2019-01-01", vol=0.02)
    led = nv.ledger(s, initial=1.0)
    r = nv.vol_target(s, nv.TARGET)
    np.testing.assert_allclose(led["equity"].pct_change().iloc[1:].to_numpy(),
                               r.iloc[1:].to_numpy(), rtol=1e-12, atol=1e-15)


def test_replay_starts_at_start_and_never_looks_ahead():
    s = series(600, start="2025-06-02")
    days = mp.replay(s)
    assert days and days[0]["date"] >= str(mp.START.date())
    assert days[0]["hold_equity"] == pytest.approx(mp.INITIAL)
    cut = pd.Timestamp(days[10]["date"])
    assert mp.replay(s[s.index <= cut]) == [d for d in days if d["date"] <= str(cut.date())]
    assert mp.replay(s[s.index < mp.START]) == []
    summ = mp.summary(days, 0)
    assert summ["paper"] is True and summ["days"] == len(days)
