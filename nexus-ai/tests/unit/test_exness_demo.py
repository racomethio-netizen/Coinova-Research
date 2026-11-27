"""Exness demo plans: lot rounding, the gold volatility sleeve (band, entries gated,
reductions always allowed), and EURUSD NQS-1 entries (sizing, stop/target, session and
spread checks) and management (time exit, break-even)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_strategies import exness_demo as plan

GOLD = plan.Spec("XAUUSDm", 100.0, 0.01, 0.01, 200.0, 0.001)
EUR = plan.Spec("EURUSDm", 100_000.0, 0.01, 0.01, 200.0, 0.00001)


def test_lots_round_down_to_the_step_and_respect_the_minimum():
    assert plan.lots(1_234.0, EUR) == 0.01
    assert plan.lots(999.0, EUR) == 0.0
    assert plan.lots(25_900.0, EUR) == 0.25


def closes(vol_daily: float, n: int = 60, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-08-01", periods=n, tz="UTC")
    return pd.Series(2000 * np.exp(np.cumsum(rng.normal(0, vol_daily, n))), index=idx)


def test_gold_sleeve_weight_band_and_gating():
    calm = closes(0.004)                           # ~6% a year: full 25% sleeve
    ins = plan.gold_plan(calm, 2000.0, 2000.3, 10_000.0, [], GOLD, True, "20261127")
    (o,) = ins
    assert o["action"] == "open" and o["side"] == "buy"
    assert o["volume"] == plan.lots(0.25 * 10_000 / 2000.3, GOLD) == 0.01
    assert o["client_id"] == "nxG20261127v1"
    assert plan.gold_plan(calm, 2000.0, 2000.3, 10_000.0, [], GOLD, False, "20261127") == []
    big = plan.gold_plan(calm, 2000.0, 2000.3, 200_000.0, [], GOLD, True, "d")
    assert big[0]["volume"] == 0.24                # 0.25 x 200,000 / 2000.3 / 100 oz
    held = [{"ticket": 7, "volume": 0.24, "side": "buy", "time": 1}]
    assert plan.gold_plan(calm, 2000.0, 2000.3, 200_000.0, held, GOLD, True, "d") == []
    wild = closes(0.03)                            # ~48% a year: weight ~0.31 x 25%
    out = plan.gold_plan(wild, 2000.0, 2000.3, 200_000.0, held, GOLD, False, "d")
    assert out and out[0]["action"] == "close" and out[0]["ticket"] == 7   # always allowed
    assert 0.1 < out[0]["volume"] < 0.24


def hourly(n: int = 1000, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-09-01", periods=n, freq="1h", tz="UTC")
    c = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.r_[1.10, c[:-1]]
    w = np.abs(rng.normal(0, 0.0006, (2, n)))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + w[0]),
                         "low": np.minimum(o, c) * (1 - w[1]), "close": c}, index=idx)


def fake_pick(monkeypatch: pytest.MonkeyPatch, d: int, kind: str = "r") -> None:
    real = plan.choose

    def pick(f: pd.DataFrame, mode: str, cfg: object) -> pd.DataFrame:
        pk = real(f, mode, cfg)
        pk.iloc[-1, pk.columns.get_loc("dir")] = d
        pk.iloc[-1, pk.columns.get_loc("conf")] = 3
        pk.iloc[-1, pk.columns.get_loc("stop_atr")] = 1.5
        pk.iloc[-1, pk.columns.get_loc("kind")] = kind
        pk.iloc[-1, pk.columns.get_loc("strategy")] = "trend"
        return pk

    monkeypatch.setattr(plan, "choose", pick)


def test_eurusd_entry_sizing_stop_target_and_checks(monkeypatch: pytest.MonkeyPatch):
    h1 = hourly()
    fake_pick(monkeypatch, 1)
    t = h1.index[-1]
    bid, ask = float(h1["close"].iloc[-1]), float(h1["close"].iloc[-1]) + 0.00008
    ok_now = t + pd.Timedelta(hours=1, minutes=5)
    if (ok_now.weekday() == 4 and ok_now.hour >= 19) or ok_now.weekday() >= 5 or \
            ok_now.hour >= 20:
        pytest.skip("synthetic last bar falls in a blocked session")
    out = plan.eurusd_plan(h1, bid, ask, 10_000.0, None, EUR, True, ok_now)
    if not out:                                     # cost/spread check refused: recompute
        pytest.fail("expected an entry with a tight spread")
    (o,) = out
    from nexus_strategies.nqs1.config import PROFILES, NQS1Config
    from nexus_strategies.nqs1.engine import prepare
    from nexus_strategies.nqs1.features import resample
    atr = float(prepare(h1, resample(h1, "H4"), PROFILES["intraday"], NQS1Config())
                ["atr"].iloc[-1])
    risk = 1.5 * atr
    assert o["side"] == "buy" and o["sl"] == pytest.approx(ask - risk, abs=1e-5)
    assert o["tp"] == pytest.approx(ask + 2 * risk, abs=1e-5)
    assert o["volume"] == plan.lots(min(0.0025 * 10_000 / risk, 0.6 * 10_000 / ask), EUR)
    assert o["client_id"].startswith(f"nxE{int(t.timestamp())}r")
    assert plan.eurusd_plan(h1, bid, ask, 10_000.0, None, EUR, False, ok_now) == []
    wide = plan.eurusd_plan(h1, bid, bid + atr, 10_000.0, None, EUR, True, ok_now)
    assert wide == []                               # spread above 15% of ATR
    stale = plan.eurusd_plan(h1, bid, ask, 10_000.0, None, EUR, True,
                             ok_now + pd.Timedelta(hours=5))
    assert stale == []                              # last closed bar too old


def test_eurusd_management_time_exit_and_break_even(monkeypatch: pytest.MonkeyPatch):
    h1 = hourly()
    fake_pick(monkeypatch, 0)                       # no opposite signal
    now = h1.index[-1] + pd.Timedelta(hours=1)
    old = {"ticket": 5, "side": "buy", "volume": 0.1, "price_open": 1.0, "sl": 0.99,
           "tp": 1.2, "time_utc": h1.index[-60], "comment": "nxE1r150"}
    (c,) = plan.eurusd_plan(h1, 1.1, 1.1001, 10_000.0, old, EUR, True, now)
    assert c["action"] == "close" and c["reason"] == "time exit" and c["ticket"] == 5
    entry = float(h1["low"].iloc[-10:].min()) - 0.003        # well in profit: > 1R
    pos = {"ticket": 6, "side": "buy", "volume": 0.1, "price_open": entry,
           "sl": entry - 0.0015, "tp": entry + 0.003, "time_utc": h1.index[-10],
           "comment": "nxE1r150"}
    (m,) = plan.eurusd_plan(h1, 1.1, 1.1001, 10_000.0, pos, EUR, False, now)
    assert m["action"] == "modify" and m["sl"] >= entry                # never loosened
