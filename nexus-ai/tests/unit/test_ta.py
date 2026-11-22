"""Phase C indicators and multi-timeframe engine.

1. Causality: every indicator is prefix-invariant (computing on more data never changes
   past values), so nothing uses the future.
2. Correctness: each indicator matches an independent loop-based reference implementation
   of its textbook formula (or a hand-checked scenario).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from nexus_analysis.mtf import mtf_frame, mtf_snapshot, tf_state
from nexus_analysis.technical import technical_snapshot
from nexus_strategies import indicators as ind
from nexus_strategies import ta


def walk(n: int = 400, seed: int = 3, freq: str = "5min") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.uniform(10, 100, n)
    idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC", name="open_time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": vol}, index=idx)


DF = walk()
INDICATORS = {
    "dmi": lambda d: ta.dmi(d), "supertrend": lambda d: ta.supertrend(d),
    "ichimoku": lambda d: ta.ichimoku(d).astype(float), "ema_set": lambda d: ta.ema_set(d["close"]),
    "roc": lambda d: ta.roc(d["close"]), "momentum": lambda d: ta.momentum(d["close"]),
    "stoch_rsi": lambda d: ta.stoch_rsi(d["close"]), "cci": lambda d: ta.cci(d),
    "keltner": lambda d: ta.keltner(d), "hv": lambda d: ta.historical_volatility(d["close"]),
    "vol_regime": lambda d: ta.volatility_regime(d, long=50, pct_window=100).astype(float),
    "obv": lambda d: ta.obv(d), "mfi": lambda d: ta.mfi(d),
    "rel_volume": lambda d: ta.relative_volume(d["volume"]),
    "vp_div": lambda d: ta.volume_price_divergence(d), "candle": lambda d: ta.candle_shape(d),
    "range": lambda d: ta.range_state(d).astype(float),
    "follow": lambda d: ta.breakout_follow_through(d),
    "equal": lambda d: ta.equal_levels(d), "wick": lambda d: ta.wick_rejection(d),
    "rsi_div": lambda d: ta.rsi_divergence(d), "tf_state": lambda d: tf_state(d),
}


@pytest.mark.parametrize("name", list(INDICATORS))
def test_prefix_invariance_no_lookahead(name: str) -> None:
    fn = INDICATORS[name]
    full = pd.DataFrame(fn(DF))
    for k in (120, 250, 333):
        part = pd.DataFrame(fn(DF.iloc[:k]))
        pd.testing.assert_frame_equal(part, full.iloc[:k], check_dtype=False, atol=1e-9,
                                      obj=f"{name} prefix {k}")


# ---------------------------------------------------------------- references
def ref_wilder(x: np.ndarray, n: int) -> np.ndarray:
    out, prev, seen = np.full(len(x), np.nan), None, 0
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        prev = v if prev is None else prev + (v - prev) / n
        seen += 1
        if seen >= n:
            out[i] = prev
    return out


def test_dmi_matches_reference_and_existing_adx() -> None:
    h, lo, c = DF["high"].to_numpy(), DF["low"].to_numpy(), DF["close"].to_numpy()
    n = 14
    tr, pdm, mdm = np.full(len(c), np.nan), np.zeros(len(c)), np.zeros(len(c))
    tr[0] = h[0] - lo[0]
    for i in range(1, len(c)):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
    pdm[0] = mdm[0] = 0.0
    atr_ = ref_wilder(tr, n)
    pdi = 100 * ref_wilder(pdm, n) / atr_
    mdi = 100 * ref_wilder(mdm, n) / atr_
    got = ta.dmi(DF)
    np.testing.assert_allclose(got["plus_di"], pdi, rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(got["minus_di"], mdi, rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(got["adx"], ind.adx(DF), rtol=1e-12, equal_nan=True)


def test_roc_momentum_obv_relvol_hv() -> None:
    c, v = DF["close"].to_numpy(), DF["volume"].to_numpy()
    roc = [np.nan] * 12 + [100 * (c[i] / c[i - 12] - 1) for i in range(12, len(c))]
    np.testing.assert_allclose(ta.roc(DF["close"]), roc, equal_nan=True)
    mom = [np.nan] * 10 + [c[i] - c[i - 10] for i in range(10, len(c))]
    np.testing.assert_allclose(ta.momentum(DF["close"]), mom, equal_nan=True)
    obv, acc = [], 0.0
    for i in range(len(c)):
        if i and c[i] > c[i - 1]:
            acc += v[i]
        elif i and c[i] < c[i - 1]:
            acc -= v[i]
        obv.append(acc)
    np.testing.assert_allclose(ta.obv(DF), obv)
    rv = [np.nan] * 20 + [v[i] / np.mean(v[i - 20:i]) for i in range(20, len(v))]
    np.testing.assert_allclose(ta.relative_volume(DF["volume"]), rv, rtol=1e-12,
                               equal_nan=True)
    lr = np.diff(np.log(c))
    hv = [np.nan] * 30 + [np.std(lr[i - 30:i], ddof=1) * np.sqrt(365)
                          for i in range(30, len(c))]
    np.testing.assert_allclose(ta.historical_volatility(DF["close"]), hv, rtol=1e-9,
                               equal_nan=True)


def test_cci_and_mfi_reference() -> None:
    h, lo, c, v = (DF[k].to_numpy() for k in ("high", "low", "close", "volume"))
    tp = (h + lo + c) / 3
    cci = [np.nan] * 19
    for i in range(19, len(tp)):
        w = tp[i - 19:i + 1]
        mad = np.mean(np.abs(w - w.mean()))
        cci.append((tp[i] - w.mean()) / (0.015 * mad))
    np.testing.assert_allclose(ta.cci(DF), cci, rtol=1e-9, equal_nan=True)
    mfi = [np.nan] * 14
    for i in range(14, len(tp)):
        pos = sum(tp[j] * v[j] for j in range(i - 13, i + 1) if tp[j] > tp[j - 1])
        neg = sum(tp[j] * v[j] for j in range(i - 13, i + 1) if tp[j] < tp[j - 1])
        mfi.append(100.0 if neg == 0 else 100 - 100 / (1 + pos / neg))
    np.testing.assert_allclose(ta.mfi(DF), mfi, rtol=1e-9, equal_nan=True)


def test_stoch_rsi_and_keltner_and_ichimoku_reference() -> None:
    r = ind.rsi(DF["close"]).to_numpy()
    raw = np.full(len(r), np.nan)
    for i in range(len(r)):
        w = r[max(0, i - 13):i + 1]
        if i >= 13 and not np.isnan(w).any() and w.max() > w.min():
            raw[i] = 100 * (r[i] - w.min()) / (w.max() - w.min())
    k = pd.Series(raw).rolling(3, min_periods=3).mean().to_numpy()
    np.testing.assert_allclose(ta.stoch_rsi(DF["close"])["k"], k, rtol=1e-9, equal_nan=True)
    kc = ta.keltner(DF)
    ok = kc["mid"].notna()
    np.testing.assert_allclose((kc["upper"] - kc["mid"])[ok], (2 * ind.atr(DF, 10))[ok])
    h, lo = DF["high"].to_numpy(), DF["low"].to_numpy()
    tenkan = [np.nan] * 8 + [(h[i - 8:i + 1].max() + lo[i - 8:i + 1].min()) / 2
                             for i in range(8, len(h))]
    np.testing.assert_allclose(ta.ichimoku(DF)["tenkan"], tenkan, equal_nan=True)
    span_b = ta.ichimoku(DF)["span_b"].to_numpy()
    i = 300                                   # value at bar i was computed at bar i - 26
    j = i - 26
    assert span_b[i] == pytest.approx((h[j - 51:j + 1].max() + lo[j - 51:j + 1].min()) / 2)


def test_supertrend_flips_with_a_trend_reversal() -> None:
    n = 120
    up = np.linspace(100, 160, n // 2)
    down = np.linspace(160, 100, n // 2)
    close = np.r_[up, down]
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
                       "volume": 1.0}, index=idx)
    st = ta.supertrend(df)
    assert st["direction"].iloc[50] == 1 and st["direction"].iloc[-1] == -1
    assert (st["supertrend"].iloc[50] < close[50]) and (st["supertrend"].iloc[-1] > close[-1])


def test_candle_shape_pivots_and_wick_rejection_by_hand() -> None:
    idx = pd.date_range("2026-01-01", periods=2, freq="1D", tz="UTC")
    df = pd.DataFrame({"open": [10.0, 10.0], "high": [12.0, 10.2], "low": [8.0, 6.0],
                       "close": [11.0, 10.1], "volume": [1.0, 1.0]}, index=idx)
    s = ta.candle_shape(df)
    assert s.iloc[0].to_dict() == pytest.approx({"body": 0.25, "upper_wick": 0.25,
                                                 "lower_wick": 0.5, "bullish": 1.0})
    assert ta.wick_rejection(df).iloc[1] == 1.0               # 4.0 of 4.2 range below body
    intraday = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                            index=pd.date_range("2026-01-02", periods=3, freq="8h", tz="UTC"))
    piv = ta.pivots_from_daily(intraday, df.iloc[:1])          # day 1 levels apply on day 2
    p = (12 + 8 + 11) / 3
    assert piv["pivot"].iloc[0] == pytest.approx(p)
    assert piv["r1"].iloc[0] == pytest.approx(2 * p - 8)
    assert piv["s2"].iloc[0] == pytest.approx(p - 4)


def _frame(prices: list[tuple[float, float, float]], freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(prices), freq=freq, tz="UTC")
    hi, lo, c = zip(*prices, strict=True)
    return pd.DataFrame({"open": c, "high": hi, "low": lo, "close": c, "volume": 1.0},
                        index=idx)


def test_equal_highs_detected_on_confirmation_bar() -> None:
    bars = [(10, 9, 9.5)] * 16
    bars[5] = (12.0, 9, 9.5)                                   # swing high 12.00
    bars[11] = (12.005, 9, 9.5)                                # swing high 12.005 (equal)
    df = _frame(bars)
    eq = ta.equal_levels(df, tol_atr=1.0)
    assert eq["equal_high"].dropna().index.tolist() == [df.index[13]]   # 11 + 2 bars
    assert eq["equal_high"].dropna().iloc[0] == pytest.approx(12.005)


def test_rsi_bullish_divergence_scenario() -> None:
    # Two dips: the second makes a lower low but with a gentler fall -> higher RSI.
    path = list(np.linspace(100, 90, 15)) + list(np.linspace(90.5, 98, 10)) \
        + [97.5, 97, 96.5, 96, 95.5, 95, 94.5, 94, 89.8] + list(np.linspace(91, 99, 10))
    df = _frame([(p + 0.1, p - 0.1, p) for p in path])
    div = ta.rsi_divergence(df)
    assert div.max() == 1.0 and div.min() == 0.0


def test_breakout_retest_and_failure() -> None:
    flat = [(101.0, 99.0, 100.0)] * 30
    retest = [*flat, (104, 101, 103.5), (104, 101.2, 103), (103, 101.05, 102.5)]
    rs = ta.breakout_follow_through(_frame(retest), n=20, tol_atr=0.5)
    assert rs["retest_up"].iloc[31:].max() == 1.0 and rs["failed_breakout_up"].sum() == 0
    fail = [*flat, (104, 101, 103.5), (103, 99.5, 100.0)]
    rs2 = ta.breakout_follow_through(_frame(fail), n=20)
    assert rs2["failed_breakout_up"].iloc[-1] == 1.0


# ---------------------------------------------------------------- multi-timeframe
def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def test_mtf_alignment_uses_only_closed_higher_bars() -> None:
    m5 = walk(2400, seed=11)
    h1 = _resample(m5, "1h")
    f = mtf_frame({"5m": m5, "1h": h1}, "5m")
    st1h = tf_state(h1)
    # The 5m bar opening 10:50 closes 10:55: the newest CLOSED 1h bar is 09:00 (closed 10:00).
    t = pd.Timestamp("2026-01-05 10:50", tz="UTC")
    assert f.loc[t, "1h_rsi"] == pytest.approx(st1h.loc[pd.Timestamp("2026-01-05 09:00",
                                                                     tz="UTC"), "rsi"])
    # The 5m bar opening 10:55 closes 11:00 together with the 10:00 1h bar: now usable.
    t2 = pd.Timestamp("2026-01-05 10:55", tz="UTC")
    assert f.loc[t2, "1h_rsi"] == pytest.approx(st1h.loc[pd.Timestamp("2026-01-05 10:00",
                                                                      tz="UTC"), "rsi"])


def test_mtf_frame_is_prefix_invariant_and_scores_are_bounded() -> None:
    m5 = walk(2400, seed=5)
    frames_full = {"5m": m5, "15m": _resample(m5, "15min"), "1h": _resample(m5, "1h")}
    full = mtf_frame(frames_full, "5m")
    cut = m5.index[1500]
    frames_part = {k: v[v.index + pd.Timedelta(k.replace("m", "min")) <= cut + pd.Timedelta("5min")]
                   for k, v in frames_full.items()}
    part = mtf_frame(frames_part, "5m")
    pd.testing.assert_frame_equal(part, full.loc[part.index], check_dtype=False, atol=1e-9)
    for col in ("trend_agreement", "momentum_agreement", "volatility_agreement",
                "structure_score", "htf_trend"):
        s = full[col].dropna()
        assert s.between(-1, 1).all() and len(s) > 1000


def test_conflict_flag_when_base_and_higher_timeframes_disagree() -> None:
    # Hourly uptrend for days, then the last few hours fall: the 5m trend turns down while
    # the 1h trend is still up.
    n = 3000
    up = np.linspace(100, 200, n - 60)
    down = np.linspace(200, 185, 60)
    close = np.r_[up, down]
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    m5 = pd.DataFrame({"open": close, "high": close * 1.0005, "low": close * 0.9995,
                       "close": close, "volume": 1.0}, index=idx)
    f = mtf_frame({"5m": m5, "1h": _resample(m5, "1h")}, "5m")
    last = f.iloc[-1]
    assert last["5m_trend"] < 0 and last["htf_trend"] > 0 and last["conflict"] == 1.0
    assert last["htf_confirm_long"] == 1.0 and last["htf_confirm_short"] == 0.0


def test_snapshots_are_json_and_honest_about_missing_history() -> None:
    m5 = walk(400)
    snap = technical_snapshot(m5, daily=_resample(m5, "1D"))
    json.dumps(snap)
    assert snap["trend"]["ema_200"] is not None and snap["trend"]["ichimoku_span_b"] is not None
    short = technical_snapshot(m5.iloc[:150])
    assert short["trend"]["ema_200"] is None                    # not enough bars
    assert technical_snapshot(m5.iloc[:30])["insufficient_history"] is True
    mtf = mtf_snapshot({"5m": m5, "15m": _resample(m5, "15min")}, "5m")
    json.dumps(mtf)
    assert set(mtf["timeframes"]) == {"5m", "15m"} and mtf["scores"]["trend_agreement"] is not None


def test_historical_volatility_is_annualized_by_bar_length() -> None:
    from nexus_analysis.technical import _periods_per_year

    m5 = walk(400)                                   # 5-minute bars
    assert _periods_per_year(m5) == pytest.approx(365 * 288)
    snap = technical_snapshot(m5)
    raw = float(np.log(m5["close"]).diff().iloc[-30:].std())
    assert snap["volatility"]["historical_vol_30"] == pytest.approx(raw * np.sqrt(365 * 288),
                                                                    rel=1e-3)
