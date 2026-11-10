import hashlib
import io
import zipfile
from datetime import UTC, datetime

import pytest

from nexus_engine.marketdata.archive import archive_url, parse_archive_csv, verify_and_extract
from nexus_engine.marketdata.types import SourceRequestError, SourceResponseError

T0_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
H_MS = 3_600_000


def make_zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-1h-2026-01.csv", csv_text)
    return buf.getvalue()


def test_url_format_and_input_validation() -> None:
    assert archive_url("https://data.binance.vision", "BTCUSDT", "1h", 2024, 3) == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-03.zip"
    )
    with pytest.raises(SourceRequestError):
        archive_url("https://x", "../etc", "1h", 2024, 3)


def test_checksum_is_enforced() -> None:
    z = make_zip("x")
    good = hashlib.sha256(z).hexdigest() + "  BTCUSDT-1h-2026-01.zip\n"
    assert verify_and_extract(z, good) == "x"
    with pytest.raises(SourceResponseError, match="mismatch"):
        verify_and_extract(z, "0" * 64)
    with pytest.raises(SourceResponseError, match="malformed"):
        verify_and_extract(z, "not-a-checksum")


def test_parses_millisecond_and_microsecond_rows_and_optional_header() -> None:
    ms = f"{T0_MS},100,102,99,101,12.5,{T0_MS + H_MS - 1},1262.5,42,6,600,0\n"
    us0 = (T0_MS + H_MS) * 1000
    us = f"{us0},101,103,100,102,10,{us0 + H_MS * 1000 - 1},1020,40,5,500,0\n"
    rows = parse_archive_csv("open_time,open,high,low,close,volume,close_time,quote,count,tb,tq,ignore\n" + ms + us, "1h")
    assert [r.open_time.hour for r in rows] == [0, 1]
    assert all(r.is_final for r in rows)


def test_rejects_inconsistent_close_time() -> None:
    with pytest.raises(SourceResponseError):
        parse_archive_csv(f"{T0_MS},1,1,1,1,1,{T0_MS + 5},1,1,0,0,0\n", "1h")
