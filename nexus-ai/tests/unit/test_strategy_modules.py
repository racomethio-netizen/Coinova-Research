"""The six strategy modules (unified brief Phase 3), on synthetic data only:
* no look-ahead: a signal is identical whether or not later bars exist;
* the planned reward/risk is never below 2:1;
* the shared trade simulator: fills on the right side of the spread, stop before target,
  gaps through the stop, time and clock exits, crypto fees, swap, and the book's limits
  (one position per symbol, at most three at a time)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest
from nexus_strategies import modules as mods
from nexus_strategies.modules import sim
from nexus_strategies.modules.base import make_signals


def fx_m5(days: int = 160, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    """A trending-then-ranging random walk on weekdays, with a spread column."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 288, freq="5min", tz="UTC")
    idx = idx[idx.day_of_week < 5]
    n = len(idx)
    drift = np.where(np.arange(n) % 20_000 < 10_000, 2e-6, -2e-6)
    c = 1.1 * np.exp(np.cumsum(rng.normal(drift, 2.5e-4, n)))
    o = np.r_[1.1, c[:-1]]
    w = np.abs(rng.normal(0, 1.5e-4, (2, n)))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + w[0]),
                         "low": np.minimum(o, c) * (1 - w[1]), "close": c,
                         "spread": np.full(n, 0.00008)}, index=idx)


def crypto_m5(days: int = 120, seed: int = 3, price: float = 30_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 288, freq="5min", tz="UTC")
    n = len(idx)
    c = price * np.exp(np.cumsum(rng.normal(3e-5, 3e-3, n)))
    o = np.r_[price, c[:-1]]
    w = np.abs(rng.normal(0, 1.5e-3, (2, n)))
    vol = rng.lognormal(3, 1.0, n) * 50
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + w[0]),
                         "low": np.minimum(o, c) * (1 - w[1]), "close": c,
                         "volume": vol}, index=idx)


def crypto_regimes(days: int = 200, seed: int = 5, price: float = 30_000.0) -> pd.DataFrame:
    """Two quiet days then one trending day, repeated, with volume bursts on big bars:
    enough consolidations and breakouts for the crypto modules to fire."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 288, freq="5min", tz="UTC")
    n = len(idx)
    phase = (np.arange(n) // 288) % 3
    mu, sd = np.where(phase < 2, 0.0, 6e-4), np.where(phase < 2, 6e-4, 2.5e-3)
    r = rng.normal(mu, sd)
    c = price * np.exp(np.cumsum(r))
    o = np.r_[price, c[:-1]]
    w = np.abs(rng.normal(0, sd / 2, (2, n)))
    vol = rng.lognormal(4, 0.5, n) * np.where(phase < 2, 1.0, 4.0) * (1 + 8 * (r > 2 * sd))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + w[0]),
                         "low": np.minimum(o, c) * (1 - w[1]), "close": c,
                         "volume": vol}, index=idx)


def case(code: str) -> tuple[pd.DataFrame, object]:
    """Synthetic data on which the module fires, and the settings to test. The crypto
    scalper's cost filter is relaxed here: it is a setting, not part of causality."""
    spec = mods.get(code)
    if code == "fx_swing_trend":
        return fx_m5(days=560, seed=11), spec.config
    if spec.market == "forex":
        return fx_m5(), spec.config
    if code == "crypto_scalp_volume":
        return crypto_regimes(), dataclasses.replace(spec.config, max_cost_of_risk=10.0)
    return crypto_regimes(), spec.config


def _frames(code: str, m5: pd.DataFrame) -> dict[str, pd.DataFrame]:
    spec = mods.get(code)
    return mods.fx_frames(m5) if spec.market == "forex" else mods.crypto_frames(m5)


BRACKET = [c for c, m in mods.MODULES.items() if m.signals is not None]


def test_registry_has_the_six_modules_of_the_brief():
    by = {(m.market, m.style) for m in mods.MODULES.values()}
    assert by == {(mk, st) for mk in ("forex", "crypto")
                  for st in ("scalping", "intraday", "swing")}
    assert all(m.long_only for m in mods.MODULES.values() if m.market == "crypto")
    assert mods.get("crypto_swing_trend").signals is None          # its own harness
    assert mods.get("crypto_breakout_retest").evidence == "earlier_fail"
    with pytest.raises(LookupError):
        mods.get("nope")


@pytest.mark.parametrize("code", BRACKET)
def test_no_look_ahead_signals_do_not_change_when_later_bars_exist(code: str):
    spec = mods.get(code)
    m5, cfg = case(code)
    full = spec.signals(_frames(code, m5), cfg)
    assert len(full) >= 10, "the synthetic data must make the module fire"
    assert (full["rr"] >= mods.MIN_RR).all()
    period = pd.Timedelta(seconds={"5m": 300, "1h": 3600, "1d": 86400}[spec.signal_tf])
    cuts = [m5.index[int(len(m5) * q)] for q in (0.55, 0.7, 0.85)]
    for cut in cuts:
        part = spec.signals(_frames(code, m5[m5.index < cut]), cfg)
        known = full[full.index + period <= cut]
        part = part[part.index + period <= cut]
        pd.testing.assert_frame_equal(known, part, check_freq=False)


def test_signal_sides_and_stops_make_sense():
    for code in BRACKET:
        spec = mods.get(code)
        m5, cfg = case(code)
        f = _frames(code, m5)
        s = spec.signals(f, cfg)
        assert not s.empty, code
        close = f[spec.signal_tf]["close"].reindex(s.index)
        assert ((s["side"] * (close - s["stop"])) > 0).all(), code   # stop on the loss side
        if spec.long_only:
            assert (s["side"] == 1).all()


def test_fx_day_runs_from_22_utc_and_drops_stubs():
    m5 = fx_m5(days=21)
    d1 = mods.fx_frames(m5)["1d"]
    assert (d1.index.hour == 22).all()
    assert (d1.index.day_of_week != 5).all()          # no Saturday-opening bins
    one = m5[(m5.index >= "2024-01-02 22:00") & (m5.index < "2024-01-03 22:00")]
    row = d1.loc[pd.Timestamp("2024-01-02 22:00", tz="UTC")]
    assert row["open"] == one["open"].iloc[0] and row["close"] == one["close"].iloc[-1]
    assert row["high"] == one["high"].max() and row["low"] == one["low"].min()


def test_make_signals_refuses_less_than_two_to_one():
    with pytest.raises(ValueError):
        make_signals(pd.DatetimeIndex([pd.Timestamp("2024-01-01", tz="UTC")]), [1], [1.0],
                     1.5, 10)


def bars(rows: list[tuple[float, float, float, float]], spread: float = 0.0,
         start: str = "2024-01-02 08:00") -> sim.Bars:
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["spread"] = spread
    return sim.Bars.from_frame(df, 300)


FX = sim.Costs("forex", slippage=0.00001)
CR = sim.Costs("crypto", slippage=0.0005, fee=0.001)


def test_long_target_pays_the_ask_and_slippage():
    b = bars([(1.1000, 1.1005, 1.0995, 1.1002), (1.1002, 1.1100, 1.1001, 1.1090)],
             spread=0.0001)
    t = sim.simulate(b, 0, 1, 1.0980, 2.0, 10, FX)
    assert t is not None
    assert t.entry == pytest.approx(1.1000 + 0.0001 + 0.00001)
    assert t.risk == pytest.approx(t.entry - 1.0980)
    assert t.reason == "target" and t.net_r == pytest.approx(2.0)


def test_stop_first_when_a_bar_touches_both_and_gaps_fill_worse():
    b = bars([(1.1000, 1.1000, 1.1000, 1.1000), (1.1000, 1.2000, 1.0000, 1.1000)])
    t = sim.simulate(b, 0, 1, 1.0990, 2.0, 10, FX)
    assert t is not None and t.reason == "stop" and t.net_r < -1
    g = bars([(1.1000, 1.1001, 1.0999, 1.1000), (1.0950, 1.0960, 1.0940, 1.0950)])
    t = sim.simulate(g, 0, 1, 1.0990, 2.0, 10, FX)
    assert t is not None and t.exit == pytest.approx(1.0950 - 0.00001)   # gap: open, worse
    assert t.net_r < -4


def test_short_uses_the_ask_for_stops_and_targets():
    b = bars([(1.1000, 1.1000, 1.1000, 1.1000), (1.1000, 1.1009, 1.0990, 1.1000)],
             spread=0.0002)
    t = sim.simulate(b, 0, -1, 1.1010, 2.0, 10, FX)
    assert t is not None and t.reason == "stop"       # ask high 1.1011 >= stop 1.1010
    assert t.exit == pytest.approx(1.1010 + 0.00001)


def test_time_clock_and_entry_beyond_stop():
    flat = [(1.1, 1.1001, 1.0999, 1.1)] * 6
    t = sim.simulate(bars(flat), 0, 1, 1.09, 2.0, 3, FX)
    assert t is not None and t.reason == "time" and t.bars == 4
    clock = int(pd.Timestamp("2024-01-02 08:10", tz="UTC").value)
    t = sim.simulate(bars(flat), 0, 1, 1.09, 2.0, 10, FX, exit_at=clock)
    assert t is not None and t.reason == "clock" and t.bars == 3
    assert sim.simulate(bars(flat), 0, 1, 1.2, 2.0, 10, FX) is None    # already beyond stop


def test_crypto_fees_and_carry():
    b = bars([(100.0, 100.0, 100.0, 100.0), (100.0, 103.0, 99.5, 102.0)])
    t = sim.simulate(b, 0, 1, 99.0, 2.0, 10, CR)
    assert t is not None and t.reason == "target"
    gross = t.target - t.entry
    assert t.net_r == pytest.approx((gross - 0.001 * (t.entry + t.target)) / t.risk)
    t2 = sim.simulate(b, 0, 1, 99.0, 2.0, 10, CR, carry=lambda a, z, s, p: 0.1)
    assert t2 is not None and t2.net_r == pytest.approx(t.net_r - 0.1 / t.risk)
    double = sim.simulate(b, 0, 1, 99.0, 2.0, 10, CR.stressed(2))
    assert double is not None and double.net_r < t.net_r


def test_book_one_per_symbol_and_three_at_a_time():
    def tr(sym: str, start: int, end: int) -> sim.Trade:
        return sim.Trade(sym, 1, start, start, 1, 0.9, 1.2, 0.1, end, 1.1, "time", 1.0, 1)
    trades = [tr("A", 0, 10), tr("A", 5, 20), tr("B", 1, 10), tr("C", 2, 10), tr("D", 3, 10),
              tr("D", 10, 30)]
    kept = [(t.symbol, t.entry_time) for t in sim.book(trades, 3)]
    assert kept == [("A", 0), ("B", 1), ("C", 2), ("D", 10)]


def test_candidates_enter_after_the_signal_bar_and_drop_stale_signals():
    b = bars([(1.1, 1.1001, 1.0999, 1.1)] * 10)
    t0 = pd.Timestamp("2024-01-02 08:00", tz="UTC")
    sig = make_signals(pd.DatetimeIndex([t0, t0 + pd.Timedelta(hours=5)]), [1, 1],
                       [1.09, 1.09], 2.0, 3)
    out = sim.candidates(b, sig, 300, 300 * 10**9, FX, symbol="EURUSD")
    assert len(out) == 1                                   # the second has no bar: stale
    assert out[0].entry_time == int((t0 + pd.Timedelta(minutes=5)).value)
