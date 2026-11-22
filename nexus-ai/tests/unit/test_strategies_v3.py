"""Strategy modules A-G: causal, valid grids, sane protective levels, rule scenarios."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest
from nexus_strategies.library_v3 import V3_GRIDS, V3_STRATEGIES, MarketStructure
from nexus_strategies.strategies import ALL_STRATEGIES


def walk5m(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.00002, 0.003, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close,
                         "volume": rng.uniform(5, 50, n) * (1 + 3 * (rng.random(n) > 0.95))},
                        index=idx)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


M5 = walk5m(30_000, 4)
FRAMES = {"5m": M5, "15m": resample(M5, "15min"), "1h": resample(M5, "1h"),
          "4h": resample(M5, "4h")}


def test_registered_and_grids_are_valid_parameters() -> None:
    assert set(V3_STRATEGIES) <= set(ALL_STRATEGIES)
    for code, cls in V3_STRATEGIES.items():
        g = V3_GRIDS[code]
        for combo in itertools.product(*g.values()):
            cls(**dict(zip(g, combo, strict=True)))          # unknown params would raise


@pytest.mark.parametrize("code", list(V3_STRATEGIES))
def test_causal_and_protective_levels(code: str) -> None:
    cls = V3_STRATEGIES[code]
    strat = cls()
    full = strat.compute(FRAMES)
    assert len(full) == len(FRAMES[strat.base_tf])
    cut = FRAMES[strat.base_tf].index[len(full) * 2 // 3]
    part_frames = {tf: f[f.index <= cut] for tf, f in FRAMES.items()}
    part = strat.compute(part_frames)
    pd.testing.assert_frame_equal(part, full.loc[part.index], check_dtype=False, atol=1e-9)
    entries = full[full["entry"]]
    close = FRAMES[strat.base_tf]["close"].reindex(entries.index)
    assert (entries["stop"] < close).all() and (entries["target"] > close).all()
    assert full["entry"].dtype == bool


def _bars(prices: list[tuple[float, float, float, float]], freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(prices), freq=freq, tz="UTC")
    o, h, lo, c = zip(*prices, strict=True)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 10.0},
                        index=idx)


def test_market_structure_enters_on_a_held_retest_of_the_broken_high() -> None:
    # A long up-trend of higher highs and higher lows (shallow 2-bar pullbacks), a close
    # above the last swing high (BOS), then a bar that dips back to that level and holds.
    path = []
    p = 100.0
    for _leg in range(40):
        for _ in range(6):
            p += 1.0
            path.append((p - 0.5, p + 0.3, p - 0.8, p))
        for _ in range(2):
            p -= 0.8
            path.append((p + 0.5, p + 0.8, p - 0.3, p))
    swing_high = max(h for _, h, _, _ in path[-8:])
    for _ in range(2):
        p += 1.0
        path.append((p - 0.8, p + 0.2, p - 1.0, p))
    path.append((p, p + 0.2, swing_high + 0.1, swing_high + 0.9))
    h1 = _bars(path)
    out = MarketStructure(mode=1, window=10, rr=2).compute({"1h": h1, "4h": resample(h1, "4h")})
    assert out["entry"].iloc[-1], "held retest of the broken high must signal"
    last = out.iloc[-1]
    assert last["stop"] < h1["close"].iloc[-1] < last["target"]
    # The same dip closing BELOW the broken level is a failure, not an entry.
    failed = _bars([*path[:-1], (p, p + 0.2, swing_high - 1.0, swing_high - 0.5)])
    out2 = MarketStructure(mode=1, window=10, rr=2).compute({"1h": failed,
                                                            "4h": resample(failed, "4h")})
    assert not out2["entry"].iloc[-1]


def test_breakout_retest_ignores_breakouts_that_fail() -> None:
    from nexus_strategies.library_v3 import BreakoutRetest

    strat = BreakoutRetest(channel=20, rv_min=0.0, stop_atr=0.5, target_atr=3.0)
    out = strat.compute(FRAMES)
    # Every entry is a retest bar whose close is above the level that was broken.
    rs_level = FRAMES["1h"]["high"].shift(1).rolling(20, min_periods=20).max()
    for ts in out.index[out["entry"]]:
        pos = FRAMES["1h"].index.get_loc(ts)
        recent_levels = rs_level.iloc[max(0, pos - 6):pos]
        assert (FRAMES["1h"]["close"].iloc[pos] > recent_levels.min())
