"""Grid regime classifier: eight classes, causal labels, grid permission per market."""

from __future__ import annotations

import numpy as np
import pandas as pd
from nexus_strategies import grid_regime as gr


def frame(close: np.ndarray, vol: float = 5_000.0, wiggle: float = 0.002) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(close), freq="h", tz="UTC")
    c = pd.Series(close, index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * (1 + wiggle),
                         "low": c * (1 - wiggle), "close": c, "volume": vol}, index=idx)


def ou_frame(n: int, seed: int, sd: float = 0.003, pull: float = 0.08) -> pd.DataFrame:
    """Mean-reverting closes with realistic intrabar wicks (overlapping candles)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = x[t - 1] * (1 - pull) + rng.normal(0, sd)
    c = 30_000 * np.exp(x)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sd, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sd, n)))
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 5_000.0},
                        index=idx)


def test_trend_and_sideways_classes():
    n = 900
    up = frame(30_000 * np.exp(np.linspace(0, 0.9, n)))
    assert gr.classify(up)["regime"].iloc[-1] == "STRONG_BULL"
    down = frame(30_000 * np.exp(np.linspace(0, -0.9, n)))
    assert gr.classify(down)["regime"].iloc[-1] == "STRONG_BEAR"
    lab = gr.classify(ou_frame(n, 1))["regime"]
    assert (lab.iloc[300:] == "SIDEWAYS").mean() > 0.6
    assert (lab.iloc[:200] == "WARMUP").all()


def test_extreme_low_liquidity_and_high_volatility():
    rng = np.random.default_rng(2)
    n = 900
    base = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)) * 0.2)
    crash = base.copy()
    crash[-1] = crash[-2] * 0.88                                   # one-bar crash
    assert gr.classify(frame(crash))["regime"].iloc[-1] == "EXTREME"
    thin = frame(base, vol=0.01)                                   # ~300 USDT per hour
    assert gr.classify(thin)["regime"].iloc[-1] == "LOW_LIQUIDITY"
    wild = frame(base, wiggle=0.002)
    wild.loc[wild.index[-30:], "high"] *= 1.03                     # ranges explode late
    wild.loc[wild.index[-30:], "low"] *= 0.97
    assert gr.classify(wild)["regime"].iloc[-1] in ("HIGH_VOLATILITY", "EXTREME")


def test_labels_are_causal():
    rng = np.random.default_rng(3)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.004, 800)))
    a = gr.classify(frame(close))
    changed = close.copy()
    changed[600:] *= 1.5
    b = gr.classify(frame(changed))
    pd.testing.assert_series_equal(a["regime"].iloc[:600], b["regime"].iloc[:600])


def test_grid_permission_and_start_conditions():
    assert gr.grid_mode("SIDEWAYS", "spot") == "long"
    assert gr.grid_mode("WEAK_BEAR", "spot") is None
    assert gr.grid_mode("WEAK_BEAR", "futures") == "short"
    assert gr.grid_mode("SIDEWAYS", "futures") == "neutral"
    for r in ("STRONG_BULL", "STRONG_BEAR", "HIGH_VOLATILITY", "LOW_LIQUIDITY", "EXTREME"):
        assert gr.grid_mode(r, "spot") is None and gr.grid_mode(r, "futures") is None
    short = gr.latest(frame(np.full(100, 30_000.0)), "spot")
    assert short["regime"] == "INSUFFICIENT_DATA" and not short["start_ok"]
    up = gr.latest(frame(30_000 * np.exp(np.linspace(0, 0.9, 900))), "spot")
    assert up["grid"] is None and not up["start_ok"] and "NO_TRADE" in up["reasons"][0]
    blocked = gr.latest(ou_frame(900, 1), "spot", imbalance=0.9)
    assert not blocked["start_ok"] and any("imbalance" in r for r in blocked["reasons"])
    assert gr.book_imbalance(10, 0, 100, 101) == 1.0
