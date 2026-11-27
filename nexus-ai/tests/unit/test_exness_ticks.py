"""Exness tick archive -> bars: bid OHLC per 5 minutes, spread, no invented bars, and
H1/D1 resampled from M5."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from nexus_backtest import exness_ticks as ex


def month_zip(rows: list[tuple[str, float, float]]) -> bytes:
    csv = '"Exness","Symbol","Timestamp","Bid","Ask"\n' + "".join(
        f'"exness","EURUSDm","{t}",{b},{a}\n' for t, b, a in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Exness_EURUSDm_2024_01.csv", csv)
    return buf.getvalue()


def test_m5_bars_from_bid_ticks_with_spread_and_no_empty_bars():
    data = month_zip([("2024-01-02 10:00:01.000Z", 1.1000, 1.1001),
                      ("2024-01-02 10:02:00.000Z", 1.1010, 1.1012),
                      ("2024-01-02 10:04:59.900Z", 1.0990, 1.0991),
                      ("2024-01-02 10:20:00.000Z", 1.1005, 1.1006),   # 10:05-10:15 empty
                      ("2024-01-02 10:21:00.000Z", 1.1003, 1.1002)])  # ask < bid: dropped
    bars, n = ex.m5_from_zip(data)
    assert n == 4
    assert list(bars.index) == [pd.Timestamp("2024-01-02 10:00", tz="UTC"),
                                pd.Timestamp("2024-01-02 10:20", tz="UTC")]
    first = bars.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == \
        (1.1000, 1.1010, 1.0990, 1.0990)
    assert first["ticks"] == 3
    assert first["spread_median"] == pytest.approx(0.0001)
    assert (first["ask_open"], first["ask_high"], first["ask_low"], first["ask_close"]) == \
        (1.1001, 1.1012, 1.0991, 1.0991)
    assert ex.symbols_for(["EURAUD"], "raw") == {"EURAUD": "EURAUD_Raw_Spread"}
    assert ex.symbols_for(["EURAUD"], "standard") == {"EURAUD": "EURAUDm"}


def test_h1_and_d1_resampled_from_m5_and_months_listing():
    idx = pd.date_range("2024-01-02 09:55", periods=4, freq="5min", tz="UTC")
    m5 = pd.DataFrame({"open": [1, 2, 3, 4], "high": [2, 3, 4, 5], "low": [0, 1, 2, 3],
                       "close": [2, 3, 4, 5], "ticks": [1, 1, 1, 1]}, index=idx,
                      dtype=float)
    h1 = ex.resample(m5, "1h")
    assert list(h1["open"]) == [1, 2] and list(h1["close"]) == [2, 5]
    assert h1.iloc[1]["high"] == 5 and h1.iloc[1]["ticks"] == 3
    assert ex.resample(m5, "1D").iloc[0]["low"] == 0
    assert ex.months((2015, 11), (2016, 2)) == [(2015, 11), (2015, 12), (2016, 1), (2016, 2)]
    assert ex.url("EURUSDm", 2024, 1).endswith("/EURUSDm/2024/01/Exness_EURUSDm_2024_01.zip")
