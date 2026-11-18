from __future__ import annotations

import io
import zipfile
from decimal import Decimal as D

import pytest
from nexus_backtest.futures_data import month_range, parse_funding_zip, parse_klines_zip


def make_zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


def test_month_range_crosses_years() -> None:
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_klines_parsed_as_exact_decimals() -> None:
    csv_text = ("open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,i\n"
                "1761955200000,100.10,101.00,99.50,100.70,12.345,0,0,0,0,0\n")
    df = parse_klines_zip(make_zip("BTCUSDT-1h-2025-10.csv", csv_text))
    assert df.close.iloc[0] == D("100.70") and df.index[0].hour == 0


def test_funding_parsed() -> None:
    csv_text = "calc_time,funding_interval_hours,last_funding_rate\n1761955200000,8,0.0001\n"
    s = parse_funding_zip(make_zip("BTCUSDT-fundingRate-2025-10.csv", csv_text))
    assert s.iloc[0] == D("0.0001")


def test_unexpected_archive_layout_refused() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.csv", "a")
    with pytest.raises(ValueError):
        parse_klines_zip(buf.getvalue())
    with pytest.raises(ValueError):
        parse_klines_zip(make_zip("notes.txt", "x"))
