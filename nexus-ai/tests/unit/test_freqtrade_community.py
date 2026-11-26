"""Harness for the Freqtrade community out-of-sample test: archive parsing (ms and us
timestamps), windows, statistics, gates, bootstrap and Holm correction."""

from __future__ import annotations

import io
import zipfile

import numpy as np
from nexus_backtest import freqtrade_community as fc


def _zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-5m-2025-01.csv", "\n".join(rows))
    return buf.getvalue()


def test_parses_millisecond_and_microsecond_archives_identically():
    ms = _zip(["1704067200000,42000,42100,41900,42050,12.5,1704067499999,0,0,0,0,0"])
    us = _zip(["1704067200000000,42000,42100,41900,42050,12.5,1704067499999999,0,0,0,0,0"])
    a, b = fc.parse_kline_zip(ms), fc.parse_kline_zip(us)
    assert a.equals(b) and str(a["date"].iloc[0]) == "2024-01-01 00:00:00+00:00"
    hdr = _zip(["open_time,open,high,low,close,volume,close_time,a,b,c,d,e",
                "1704067200000,1,2,0.5,1.5,3,0,0,0,0,0,0"])
    assert len(fc.parse_kline_zip(hdr)) == 1


def test_window_starts_after_publication():
    assert fc.window("2018-05-01") == ("20240101", False)
    assert fc.window(None) == ("20240101", False)
    assert fc.window("2026-04-15") == ("20260501", True)
    assert fc.window("2025-12-20") == ("20260101", True)


def _trades(profits: list[float], start: str = "2024-02-01") -> list[dict]:
    days = np.linspace(0, 900, len(profits)).astype(int)
    base = np.datetime64(start)
    return [{"pair": "BTC/USDT" if i % 2 else "ETH/USDT",
             "open_date": str(base + np.timedelta64(int(d), "D")),
             "close_date": str(base + np.timedelta64(int(d), "D") + np.timedelta64(1, "h")),
             "profit_ratio": p / 3333, "profit_abs": p, "fee_open": 0.001, "fee_close": 0.001,
             "stake_amount": 3333.0, "exit_reason": "roi", "trade_duration": 60}
            for i, (p, d) in enumerate(zip(profits, days, strict=True))]


def test_stats_and_gates_pass_a_steady_winner():
    rng = np.random.default_rng(1)
    profits = list(rng.normal(12, 20, 120))
    s = fc.stats(_trades(profits), "20240101")
    assert s["trades"] == 120 and s["net_profit"] > 0 and s["profit_factor"] > 1.2
    assert set(s["by_year"]) <= {"2024", "2025", "2026"}
    assert fc.gates(s, {"net_profit": 1.0}) == []


def test_gates_name_every_failure():
    s = fc.stats(_trades([-50.0, 20.0] * 10), "20240101")
    fails = fc.gates(s, {"net_profit": -1.0})
    joined = " ".join(fails)
    for word in ("net profit", "profit factor", "trades", "2x fees"):
        assert word in joined


def test_bootstrap_and_holm():
    rng = np.random.default_rng(3)
    good = rng.normal(0.01, 0.02, 400)
    noise = rng.normal(0.0, 0.02, 400)
    pg, pn = fc.bootstrap_p(good), fc.bootstrap_p(noise)
    assert pg is not None and pg < 0.001 and pn is not None and pn > 0.05
    assert fc.bootstrap_p(np.array([0.1, 0.2])) is None
    sig = fc.holm({"a": 0.001, "b": 0.03, "c": 0.04, "d": None})
    assert sig == {"a": True, "b": False, "c": False, "d": False}   # 0.03 > 0.05/2, then stop
    assert fc.holm({"a": 0.001, "b": 0.02})["b"] is True             # 0.02 <= 0.05/1


def test_months_range():
    m = fc.months("2023-11", "2024-02")
    assert m == ["2023-11", "2023-12", "2024-01", "2024-02"]
