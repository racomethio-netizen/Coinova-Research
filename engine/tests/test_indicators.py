"""Indicators: golden values against TA-Lib (test-only dependency) and causality.

Series are synthetic random walks (test data only)."""

import numpy as np
import pytest
import talib
from hypothesis import given, settings
from hypothesis import strategies as st

from nexus_engine.signals import indicators as ta
from nexus_engine.signals.levels import levels_at, swing_points


def ohlcv(n: int, seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = rng.lognormal(3, 0.5, n)
    return open_, high, low, close, vol


def assert_matches(ours: np.ndarray, ref: np.ndarray, rtol: float = 1e-9, after: int = 0) -> None:
    assert np.array_equal(np.isnan(ours[after:]), np.isnan(ref[after:])), "NaN warm-up differs"
    np.testing.assert_allclose(ours[after:], ref[after:], rtol=rtol, equal_nan=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_golden_against_talib(seed: int) -> None:
    _, h, lo, c, _ = ohlcv(600, seed)
    for n in (20, 50, 200):
        assert_matches(ta.ema(c, n), talib.EMA(c, n))
        assert_matches(ta.sma(c, n), talib.SMA(c, n))
    assert_matches(ta.rsi(c, 14), talib.RSI(c, 14))
    assert_matches(ta.atr(h, lo, c, 14), talib.ATR(h, lo, c, 14))
    up, mid, dn = ta.bollinger(c, 20, 2.0)
    tu, tm, td = talib.BBANDS(c, 20, 2.0, 2.0, 0)
    for a, b in ((up, tu), (mid, tm), (dn, td)):
        assert_matches(a, b, rtol=1e-8)
    # TA-Lib seeds MACD's EMAs from a later start; the series converge after warm-up.
    line, sig, hist = ta.macd(c)
    tl, ts, th = talib.MACD(c, 12, 26, 9)
    for a, b in ((line, tl), (sig, ts), (hist, th)):
        np.testing.assert_allclose(a[300:], b[300:], rtol=1e-6, atol=1e-9)


def test_rsi_edge_cases() -> None:
    assert np.isnan(ta.rsi(np.arange(10.0), 14)).all()
    up = ta.rsi(np.arange(1.0, 40.0), 14)
    assert up[-1] == 100.0
    flat = ta.rsi(np.full(40, 5.0), 14)
    assert flat[-1] == 50.0


@settings(max_examples=25, deadline=None)
@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=30, max_value=299))
def test_indicators_are_causal(seed: int, k: int) -> None:
    """f(x)[:k] == f(x[:k]): a value never changes when future bars arrive."""
    _, h, lo, c, v = ohlcv(300, seed)
    pairs = [
        (lambda s: ta.ema(c[s], 20), None),
        (lambda s: ta.rsi(c[s], 14), None),
        (lambda s: ta.atr(h[s], lo[s], c[s], 14), None),
        (lambda s: ta.macd(c[s])[2], None),
        (lambda s: ta.bollinger(c[s])[0], None),
        (lambda s: ta.volume_ratio(v[s], 20), None),
    ]
    full, part = slice(None), slice(0, k)
    for f, _ in pairs:
        np.testing.assert_array_equal(f(full)[:k], f(part))


def test_swing_points_are_known_only_after_confirmation() -> None:
    _, h, lo, _, _ = ohlcv(200, 7)
    highs, lows = swing_points(h, lo, 3)
    assert highs and lows
    for t in (50, 100, 150):
        res, sup = levels_at(h, lo, t, atr_t=1.0)
        assert all(lv.last_confirmed_index <= t for lv in res + sup)
        # Levels at t are identical whether or not later bars exist.
        res2, sup2 = levels_at(h[: t + 1], lo[: t + 1], t, atr_t=1.0)
        assert (res, sup) == (res2, sup2)
