"""NEXUS bot strategy: no look-ahead, explicit checks, levels and exits."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_strategies.bot_strategy import BotParams, latest, signals


def m5(n: int = 4000, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    drift = np.repeat(rng.choice([-0.0006, 0.0, 0.0008], size=n // 300 + 1), 300)[:n]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.003, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.002,
                         "low": np.minimum(open_, close) * 0.998, "close": close,
                         "volume": rng.lognormal(5, 0.6, n)}, index=idx)


DF = m5()


@pytest.mark.parametrize("cut", [1500, 2600, 3333])
def test_signals_never_use_future_bars(cut: int) -> None:
    full = signals(DF)
    part = signals(DF.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part, check_dtype=False)


def test_entries_meet_every_check_and_have_sane_levels() -> None:
    s = signals(DF)
    e = s[s.entry]
    assert len(e) > 0
    checks = [c for c in s.columns if c.startswith("chk_")]
    assert len(checks) == 7 and e[checks].all(axis=None)
    assert (e.stop < e.close).all() and (e.target > e.close).all()
    r = e.close - e.stop
    assert np.allclose(e.target - e.close, 2.0 * r)                      # take profit at 2R
    assert ((e.target - e.close) / e.close >= 3 * 0.003 - 1e-12).all()   # beats costs


def test_strict_cost_filter_blocks_everything() -> None:
    assert not signals(DF, BotParams(min_edge_x=1000)).entry.any()


def test_latest_explains_no_trade() -> None:
    i = int(np.flatnonzero(~signals(DF).entry.to_numpy())[-1])
    reg, (c,) = latest("BTC/USDT", DF.iloc[: i + 1])
    assert c["entry"] is False and c["reason"].endswith("not met")
    assert {"chk_trend_5m", "chk_volume", "chk_beats_costs"} <= set(c)
    assert reg["regime"].iloc[-1] in ("TRADEABLE", "FILTERED")
