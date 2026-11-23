"""Forward paper test of the crypto trends ensemble: replay and review summary."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from nexus_backtest import trend_paper as tp


def bars(days_after_start: int, seed: int = 0) -> dict[str, pd.DataFrame]:
    end = pd.Timestamp(tp.START) + pd.Timedelta(days=days_after_start - 1)
    idx = pd.date_range(end - pd.Timedelta(days=999), end, freq="1D", tz="UTC")
    out = {}
    for k, sym in enumerate(tp.SYMBOLS):
        rng = np.random.default_rng(seed + k)
        c = 100 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.03, len(idx))))
        s = pd.Series(c, index=idx)
        out[sym] = pd.DataFrame({"open": s.shift(1).fillna(s.iloc[0]), "close": s})
    return out


def test_nothing_before_the_start() -> None:
    assert tp.replay(bars(0)) == []


def test_replay_starts_at_start_and_is_deterministic() -> None:
    full = bars(45)
    b = {k: v.iloc[:-15] for k, v in full.items()}      # the same prices, 15 days fewer
    days = tp.replay(b)
    assert len(days) == 30 and days[0]["date"] == "2026-11-24"
    assert days[0]["equity"] == tp.INITIAL                    # no fill on the first day
    assert days == tp.replay(b)                               # same data, same record
    # More data later never changes the days already recorded (no look-ahead).
    longer = tp.replay(full)
    assert len(longer) == 45
    assert [d["equity"] for d in longer[:30]] == [d["equity"] for d in days]


def test_summary_checks_and_decision() -> None:
    days = tp.replay(bars(30))
    now = datetime(2026, 12, 24, tzinfo=UTC)
    s = tp.summary(days, divergences=0, gap_days=[], now=now)
    assert s["days"] == 30 and s["decision"] == "pending" and s["checks"]["integrity"]
    s = tp.summary(days, divergences=1, gap_days=["2026-11-25", "2026-11-26", "2026-11-27",
                                                   "2026-11-28"], now=now)
    assert s["checks"]["integrity"] is False and s["checks"]["gaps_ok"] is False
    later = datetime(2027, 6, 1, tzinfo=UTC)
    assert tp.summary(days, 0, [], later)["decision"].startswith("due:")
    assert tp.summary([], 0, [], now)["message"].startswith("waiting")
