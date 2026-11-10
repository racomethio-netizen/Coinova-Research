"""Binance Vision monthly kline archives, for historical backfill.

Each archive has a published SHA-256 checksum; an archive that does not match
is rejected. Since 2025 the spot archives use microsecond timestamps; both
millisecond and microsecond rows are accepted. The archive terms (Binance
Vision Terms of Use) are part of the source's terms review (gate G-4).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from ..timeframes import TIMEFRAMES, close_time_for
from .types import Candle, SourceRequestError, SourceResponseError

_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def archive_url(base_url: str, symbol: str, tf: str, year: int, month: int) -> str:
    if not _SYMBOL.match(symbol) or tf not in TIMEFRAMES or not (2017 <= year <= 2100 and 1 <= month <= 12):
        raise SourceRequestError("invalid archive coordinates")
    name = f"{symbol}-{tf}-{year:04d}-{month:02d}.zip"
    return f"{base_url.rstrip('/')}/data/spot/monthly/klines/{symbol}/{tf}/{name}"


def _ts(value: str, field: str) -> datetime:
    try:
        n = int(value)
    except ValueError:
        raise SourceResponseError(f"{field}: not an integer") from None
    if n < 0:
        raise SourceResponseError(f"{field}: negative")
    # >= 1e14 can only be microseconds for any plausible date (ms reach 1e14 in year 5138).
    seconds = n / 1_000_000 if n >= 100_000_000_000_000 else n / 1000
    return datetime.fromtimestamp(seconds, tz=UTC)


def _dec(value: str, field: str) -> Decimal:
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise SourceResponseError(f"{field}: not a decimal") from None
    if not d.is_finite():
        raise SourceResponseError(f"{field}: not finite")
    return d


def parse_archive_csv(text: str, tf: str) -> list[Candle]:
    candles: list[Candle] = []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if not row:
            continue
        if i == 0 and not row[0].strip().isdigit():
            continue  # optional header row
        if len(row) < 9:
            raise SourceResponseError("archive row: unexpected shape")
        open_time = _ts(row[0], "open_time")
        expected_close = close_time_for(open_time, tf)
        close_time = _ts(row[6], "close_time")
        if not (expected_close <= close_time < expected_close + timedelta(milliseconds=1)):
            raise SourceResponseError("archive row: unexpected close time")
        candles.append(
            Candle(
                open_time=open_time,
                close_time=expected_close,
                open=_dec(row[1], "open"),
                high=_dec(row[2], "high"),
                low=_dec(row[3], "low"),
                close=_dec(row[4], "close"),
                volume=_dec(row[5], "volume"),
                quote_volume=_dec(row[7], "quote_volume"),
                trade_count=int(row[8]),
                is_final=True,
            )
        )
    return candles


def verify_and_extract(zip_bytes: bytes, checksum_text: str) -> str:
    expected = checksum_text.strip().split()[0].lower() if checksum_text.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SourceResponseError("archive checksum file is malformed")
    if hashlib.sha256(zip_bytes).hexdigest() != expected:
        raise SourceResponseError("archive checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise SourceResponseError("archive must contain exactly one CSV")
        info = zf.getinfo(names[0])
        if info.file_size > MAX_ARCHIVE_BYTES:
            raise SourceResponseError("archive too large")
        return zf.read(names[0]).decode("utf-8")


def fetch_month(http: httpx.Client, base_url: str, symbol: str, tf: str, year: int, month: int) -> list[Candle]:
    url = archive_url(base_url, symbol, tf, year, month)
    zr = http.get(url)
    if zr.status_code == 404:
        return []
    if zr.status_code != 200:
        raise SourceRequestError(f"archive HTTP {zr.status_code}")
    if len(zr.content) > MAX_ARCHIVE_BYTES:
        raise SourceResponseError("archive too large")
    cr = http.get(url + ".CHECKSUM")
    if cr.status_code != 200:
        raise SourceRequestError(f"checksum HTTP {cr.status_code}")
    return parse_archive_csv(verify_and_extract(zr.content, cr.text), tf)
