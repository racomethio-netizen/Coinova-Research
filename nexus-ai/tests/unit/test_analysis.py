"""Market analysis on constructed price paths with known answers, plus causality checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_analysis import (
    liquidity_sweeps,
    market_structure,
    rolling_correlation,
    supply_demand_zones,
    support_resistance,
    swings,
)


def bars(points: list[float], freq: str = "1h", wick: float = 0.2) -> pd.DataFrame:
    """Candles from a close path. Wicks get small seeded noise so that swing extremes are
    unique, as real candles are (fractal swings require a single highest/lowest bar)."""
    idx = pd.date_range("2026-01-01", periods=len(points), freq=freq, tz="UTC", name="open_time")
    c = np.asarray(points, dtype=float)
    o = np.r_[c[0], c[:-1]]
    rng = np.random.default_rng(42)
    up = wick + rng.uniform(0, 0.05, len(c))
    dn = wick + rng.uniform(0, 0.05, len(c))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + up,
                         "low": np.minimum(o, c) - dn, "close": c,
                         "volume": 1.0}, index=idx)


# Zig-zag: up to 110, down to 100, up to 120 (HH, HL) then down through 100 (CHoCH down).
# Higher highs and higher lows (110 -> 120 -> 128; 104 -> 112), then a close below the
# last higher low (112): a change of character to the downside.
UPTREND_THEN_BREAK = [100, 104, 108, 110, 106, 104, 108, 115, 120, 116, 112, 114, 120, 128,
                      124, 120, 116, 112, 108, 104]


def test_swings_confirmed_only_after_right_bars() -> None:
    df = bars(UPTREND_THEN_BREAK)
    sw = swings(df, left=2, right=2)
    highs = sw[sw.kind == "high"]
    peak_time = df.index[3]  # the 110 peak
    confirm_time = df.index[3 + 2]  # confirmed two bars later, never on its own bar
    assert confirm_time in highs.index
    assert peak_time not in highs.index
    assert highs.loc[confirm_time, "swing_time"] == peak_time
    assert highs.loc[confirm_time, "price"] == pytest.approx(110, abs=0.3)  # includes the wick


def test_structure_detects_uptrend_bos_and_choch() -> None:
    df = bars(UPTREND_THEN_BREAK)
    st = market_structure(df, left=2, right=2)
    assert "up" in set(st.trend.tolist())
    events = st.event.dropna().tolist()
    assert events.index("bos_up") < events.index("choch_down")
    assert st.trend.iloc[-1] == "down"


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Missing values compare as the same token regardless of dtype or None/NaN."""
    return frame.astype(object).where(frame.notna(), "none").astype(str)


def test_structure_is_causal() -> None:
    df = bars([*UPTREND_THEN_BREAK, 88, 84, 80, 76, 72, 70, 74, 80, 88, 96])
    full = market_structure(df)
    for cut in (8, 12, 17):
        part = market_structure(df.iloc[:cut])
        # Missing labels compare as "none": pandas may represent None as NaN.
        pd.testing.assert_frame_equal(_normalise(full.iloc[:cut]), _normalise(part))


def test_support_resistance_strength_and_side() -> None:
    # Two swing lows near 100 make a stronger support than a single high near 120.
    path = [110, 104, 100, 104, 110, 116, 120, 116, 110, 104, 100.1, 104, 110, 108, 106]
    df = bars(path, wick=0.0)
    sr = support_resistance(df, tolerance_atr=0.5)
    last = sr.iloc[-1]
    assert last.support == pytest.approx(100.1, abs=0.3)
    assert last.support_strength == 2
    assert last.support < df.close.iloc[-1]


def test_liquidity_sweep_low_requires_close_back_inside() -> None:
    path = [110, 104, 100, 104, 110, 104, 100, 104, 110, 104, 98, 102, 106]
    df = bars(path, wick=0.0)
    df.loc[df.index[10], "low"] = 97.0  # wick below the prior swing low at 100
    df.loc[df.index[10], "close"] = 101.0  # closes back above it
    df.loc[df.index[10], "open"] = 104.0
    sw = liquidity_sweeps(df, left=2, right=2)
    assert bool(sw.sweep_low.iloc[10])
    assert sw.swept_low_level.iloc[10] == pytest.approx(100.0, abs=0.3)
    assert not sw.sweep_low.iloc[-1]


def test_liquidity_sweep_fails_when_price_closes_below() -> None:
    path = [110, 104, 100, 104, 110, 104, 100, 104, 110, 104, 98, 96, 94]
    df = bars(path, wick=0.0)
    df.loc[df.index[10], "low"] = 97.0
    df.loc[df.index[10], "close"] = 98.0
    sw = liquidity_sweeps(df, left=2, right=2)
    assert not sw.sweep_low.iloc[10]


def test_demand_zone_from_rally_and_mitigation() -> None:
    # Flat, a bearish base candle, then a strong rally, then price returns into the zone.
    path = [100, 100, 100, 99, 100, 106, 112, 118, 112, 104, 98]
    df = bars(path, wick=0.1)
    df.loc[df.index[3], "open"] = 101.0  # clearly bearish base candle
    zones = supply_demand_zones(df, impulse_bars=3, impulse_atr=1.5, atr_len=3)
    demand = zones[zones.kind == "demand"]
    assert not demand.empty
    z = demand.iloc[0]
    assert z.base_time == df.index[3]
    assert z.low == pytest.approx(df.low.iloc[3]) and z.high == pytest.approx(df.high.iloc[3])
    assert pd.notna(z.mitigated_at)  # price closed back through the zone


def test_correlation_of_identical_series_is_one_and_causal() -> None:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC")
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))), index=idx)
    alt = btc * np.exp(rng.normal(0, 0.0002, 400))
    corr = rolling_correlation(alt, btc, window=50)
    assert corr.iloc[-1] > 0.98
    assert corr.iloc[:50].isna().all()
    full = rolling_correlation(alt, btc, window=50)
    part = rolling_correlation(alt.iloc[:300], btc.iloc[:300], window=50)
    pd.testing.assert_series_equal(full.iloc[:300], part, check_names=False)


def test_clusters_do_not_chain_into_one_level() -> None:
    from nexus_analysis.levels import _cluster

    # A long run of prices, each within tolerance of the next, must NOT form one level.
    prices = [100.0 + 0.4 * i for i in range(20)]
    levels = _cluster(prices, tol=1.0)
    assert len(levels) > 1
    assert max(strength for _, strength in levels) <= 3
    assert sum(strength for _, strength in levels) == 20
