"""Strategy engine tests, centered on look-ahead safety."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_ai.features import build_features
from nexus_strategies import ALL_STRATEGIES
from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher, to_frame
from nexus_strategies.regime import classify


def synthetic(n: int, freq: str, seed: int = 1, start: str = "2025-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="open_time")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.003, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.003, n))
    vol = rng.lognormal(3, 0.5, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol},
                        index=idx)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    m5 = synthetic(12 * 24 * 60, "5min")
    return {"5m": m5, "15m": resample(m5, "15min"), "1h": resample(m5, "1h"),
            "4h": resample(m5, "4h")}


def truncate(frames: dict[str, pd.DataFrame], cutoff: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Only bars that have CLOSED by `cutoff` (what live trading would have)."""
    secs = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
    return {tf: df[df.index + pd.Timedelta(seconds=secs[tf]) <= cutoff] for tf, df in frames.items()}


# ------------------------------------------------------------------ indicators
def test_indicator_known_values() -> None:
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert ind.sma(s, 3).iloc[-1] == 9
    assert ind.rsi(s, 3).iloc[-1] == 100  # only gains
    falling = pd.Series(np.arange(30, 0, -1, dtype=float))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0)
    df = pd.DataFrame({"high": [11.0] * 20, "low": [9.0] * 20, "close": [10.0] * 20})
    assert ind.atr(df, 14).iloc[-1] == pytest.approx(2.0)
    assert np.isnan(ind.donchian_high(pd.Series([1.0, 2, 3]), 3).iloc[-1])  # excludes current


@pytest.mark.parametrize("name", ["ema", "rsi", "atr", "adx", "macd", "bollinger", "volume_z",
                                  "rolling_percentile", "donchian_high"])
def test_indicators_are_causal(frames: dict[str, pd.DataFrame], name: str) -> None:
    df = frames["15m"].iloc[:3000]
    fn = {
        "ema": lambda d: ind.ema(d["close"], 20), "rsi": lambda d: ind.rsi(d["close"]),
        "atr": lambda d: ind.atr(d), "adx": lambda d: ind.adx(d),
        "macd": lambda d: ind.macd(d["close"])["hist"],
        "bollinger": lambda d: ind.bollinger(d["close"])["width"],
        "volume_z": lambda d: ind.volume_z(d["volume"]),
        "rolling_percentile": lambda d: ind.rolling_percentile(d["close"], 200),
        "donchian_high": lambda d: ind.donchian_high(d["high"], 20),
    }[name]
    full = fn(df)
    part = fn(df.iloc[:2000])
    pd.testing.assert_series_equal(full.iloc[:2000], part, check_names=False)


# ------------------------------------------------------------------ MTF alignment
def test_higher_timeframe_bar_only_after_it_closes() -> None:
    base_idx = pd.date_range("2026-01-01", periods=12, freq="15min", tz="UTC")
    base = pd.DataFrame({"close": range(12)}, index=base_idx, dtype=float)
    h_idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    higher = pd.DataFrame({"close": [100.0, 200.0, 300.0]}, index=h_idx)
    out = align_higher(base, "15m", higher, "1h", "h_")
    # 15m bar 00:00-00:15 closes at 00:15: the 00:00 1h bar (closes 01:00) is unknown.
    assert out["h_close"].iloc[:3].isna().all()
    # 15m bar 00:45 closes 01:00 == 1h bar close: now known.
    assert out.loc[base_idx[3], "h_close"] == 100.0
    assert out.loc[base_idx[6], "h_close"] == 100.0
    assert out.loc[base_idx[7], "h_close"] == 200.0


def test_to_frame_validates() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace as NS
    c = NS(open_time=datetime(2026, 1, 1, tzinfo=UTC), open=1, high=1, low=1, close=1, volume=1)
    assert len(to_frame([c])) == 1
    with pytest.raises(ValueError):
        to_frame([c, c])


# ------------------------------------------------------------------ strategies
@pytest.mark.parametrize("code", list(ALL_STRATEGIES))
def test_strategy_signals_are_causal(frames: dict[str, pd.DataFrame], code: str) -> None:
    strat = ALL_STRATEGIES[code](min_edge_x=0.0)
    full = strat.compute(frames)
    cutoff = frames["5m"].index[0] + pd.Timedelta(days=40)
    part = strat.compute(truncate(frames, cutoff))
    common = part.index
    pd.testing.assert_frame_equal(full.loc[common], part, check_dtype=False)


@pytest.mark.parametrize("code", list(ALL_STRATEGIES))
def test_strategy_output_contract(frames: dict[str, pd.DataFrame], code: str) -> None:
    out = ALL_STRATEGIES[code](min_edge_x=0.0).compute(frames)
    assert list(out.columns) == ["entry", "exit", "stop", "target", "confidence", "regime"]
    entries = out[out.entry]
    close = frames[ALL_STRATEGIES[code].base_tf]["close"]
    assert (entries.stop < close.loc[entries.index]).all()
    assert (entries.target > close.loc[entries.index]).all()
    assert (entries.regime != "high_vol").all()
    assert out.confidence.between(0, 1).all()


def test_cost_filter_suppresses_small_targets(frames: dict[str, pd.DataFrame]) -> None:
    loose = ALL_STRATEGIES["mean_reversion"](min_edge_x=0.0).compute(frames).entry.sum()
    strict = ALL_STRATEGIES["mean_reversion"](min_edge_x=50.0).compute(frames).entry.sum()
    assert strict < loose or loose == 0
    assert strict == 0


def test_use_exit_toggle(frames: dict[str, pd.DataFrame]) -> None:
    assert not ALL_STRATEGIES["trend_following"](use_exit=0).compute(frames).exit.any()


def test_unknown_param_rejected() -> None:
    with pytest.raises(ValueError):
        ALL_STRATEGIES["momentum"](nonsense=1.0)


def test_regime_labels(frames: dict[str, pd.DataFrame]) -> None:
    r = classify(frames["1h"])
    assert set(r.dropna().unique()) <= {"trend_up", "trend_down", "range", "high_vol"}


def test_ai_features_are_causal(frames: dict[str, pd.DataFrame]) -> None:
    full = build_features(frames)
    cutoff = frames["5m"].index[0] + pd.Timedelta(days=45)
    part = build_features(truncate(frames, cutoff))
    # rvol_rank uses a 2000-bar rolling rank: equal on the overlap once both are warm.
    pd.testing.assert_frame_equal(full.loc[part.index], part, check_dtype=False)
