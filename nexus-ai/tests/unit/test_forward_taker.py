"""Forward taker-flow test: closed bars only, range-only filter, completion rule, summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from nexus_backtest import forward_taker as ft

NOW = datetime(2026, 11, 25, 12, 30, tzinfo=UTC)


def test_klines_frame_keeps_only_closed_bars() -> None:
    t0 = int(datetime(2026, 11, 25, 11, 0, tzinfo=UTC).timestamp() * 1000)
    rows = [[t0, "1", "2", "0.5", "1.5", "10", t0 + 3_599_999, "0", 0, "6", "0", "0"],
            [t0 + 3_600_000, "1", "2", "0.5", "1.5", "10", t0 + 7_199_999, "0", 0, "4", "0", "0"]]
    df = ft.klines_frame(rows, NOW)                   # second bar closes at 13:00 > now
    assert len(df) == 1 and df["taker"].iloc[0] == 6.0


def _trades() -> pd.DataFrame:
    def ts(h: float) -> pd.Timestamp:
        return pd.Timestamp(NOW - timedelta(hours=h))
    rows = [
        # signal_time, entry, exit, outcome, regime
        (ts(80), ts(79), ts(70), "target", "range"),     # signal before START -> ignored
        (ts(30), ts(29.9), ts(20), "stop", "range"),     # completed range trade -> counted
        (ts(30), ts(29.9), ts(20), "stop", "trend_up"),  # wrong regime -> ignored
        (ts(10), ts(9.9), ts(0.2), "timeout", "range"),  # data ran out: still open
    ]
    idx = pd.DatetimeIndex([r[0] for r in rows], name="signal_time")
    return pd.DataFrame({"entry_time": [r[1] for r in rows], "exit_time": [r[2] for r in rows],
                         "entry": 100.0, "exit": 101.0, "stop": 95.0, "target": 110.0,
                         "ret": 0.01, "outcome": [r[3] for r in rows], "win": True,
                         "regime": [r[4] for r in rows], "confidence": 0.5}, index=idx)


def test_evaluate_counts_only_completed_range_trades_after_start(
        monkeypatch: pytest.MonkeyPatch) -> None:
    trades = _trades()
    monkeypatch.setattr(ft, "study_signals", lambda *a, **k: trades)
    last = pd.Timestamp(NOW)
    frames = {"5m": pd.DataFrame(index=pd.DatetimeIndex([last]))}
    done, open_ = ft.evaluate(frames)
    assert NOW - timedelta(hours=80) < ft.START < NOW - timedelta(hours=30)
    assert [d["outcome"] for d in done] == ["stop"]      # only the post-start range trade
    assert [o["outcome"] for o in open_] == ["timeout"]
    assert all(d["regime"] == "range" for d in done + open_)


def test_summary_statistics_and_stopping_rule() -> None:
    trades = [{"ret": r} for r in [0.02, -0.01, 0.015, -0.005]]
    s = ft.summary(trades, gaps=2, now=NOW)
    assert s["trades"] == 4 and s["decision"] == "pending" and s["gap_hours"] == 2
    assert abs(s["mean_return"] - 0.005) < 1e-12 and abs(s["profit_factor"] - 3.5 / 1.5) < 1e-9
    assert s["t_stat"] is not None
    many = ft.summary([{"ret": 0.001}] * 30, 0, NOW)
    assert many["decision"] == "due"
    assert ft.summary([], 0, NOW)["mean_return"] is None
