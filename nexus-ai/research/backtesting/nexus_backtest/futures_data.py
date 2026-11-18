"""Download and parse Binance USD-M futures archives (public, no account needed).

Sources (data.binance.vision): monthly 1h klines, mark-price klines and funding rates.
Zip contents are untrusted: only the single expected CSV member is read, with a size cap,
and it is parsed with the csv module (no code is executed).
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/monthly"
MAX_ZIP_BYTES = 20_000_000
MAX_MEMBER_BYTES = 50_000_000


@dataclass(frozen=True)
class FuturesFrames:
    klines: pd.DataFrame        # open/high/low/close/volume, UTC index (bar open time)
    mark: pd.DataFrame          # mark_high/mark_low/mark_close, UTC index
    funding: pd.Series          # funding rate at settlement time, UTC index


def month_range(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m").replace(tzinfo=UTC)
    e = datetime.strptime(end, "%Y-%m").replace(tzinfo=UTC)
    out, cur = [], s
    while cur <= e:
        out.append(cur.strftime("%Y-%m"))
        cur = cur.replace(year=cur.year + (cur.month // 12), month=cur.month % 12 + 1)
    return out


def _read_zip_csv(data: bytes, expected_suffix: str) -> list[list[str]]:
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("archive too large")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        name = members[0].filename if members else ""
        if len(members) != 1 or not name.endswith(expected_suffix) or "/" in name:
            raise ValueError("unexpected archive layout")
        if members[0].file_size > MAX_MEMBER_BYTES:
            raise ValueError("member too large")
        text = zf.read(members[0]).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    return [r for r in rows if r and not r[0].lower().startswith(("open_time", "calc_time"))]


def _klines_frame(rows: list[list[str]]) -> pd.DataFrame:
    idx = [datetime.fromtimestamp(int(r[0]) / 1000, tz=UTC) for r in rows]
    cols = {
        "open": [Decimal(r[1]) for r in rows], "high": [Decimal(r[2]) for r in rows],
        "low": [Decimal(r[3]) for r in rows], "close": [Decimal(r[4]) for r in rows],
        "volume": [Decimal(r[5]) for r in rows],
    }
    return pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="open_time"))


def parse_klines_zip(data: bytes) -> pd.DataFrame:
    return _klines_frame(_read_zip_csv(data, ".csv"))


def parse_funding_zip(data: bytes) -> pd.Series:
    rows = _read_zip_csv(data, ".csv")
    idx = [datetime.fromtimestamp(int(r[0]) / 1000, tz=UTC) for r in rows]
    return pd.Series([Decimal(r[2]) for r in rows], index=pd.DatetimeIndex(idx, name="funding_time"),
                     name="funding_rate")


async def fetch_month(client: httpx.AsyncClient, url: str) -> bytes | None:
    r = await client.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


async def load_futures(symbol: str, start: str, end: str, cache_dir: Path) -> FuturesFrames:
    """Load 1h klines, mark-price klines and funding for ``symbol`` between two months
    (YYYY-MM, inclusive). Downloads are cached on disk; missing months raise."""
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    months = month_range(start, end)
    kl, mk, fu = [], [], []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for m in months:
            urls = {
                "kl": f"{BASE}/klines/{symbol}/1h/{symbol}-1h-{m}.zip",
                "mk": f"{BASE}/markPriceKlines/{symbol}/1h/{symbol}-1h-{m}.zip",
                "fu": f"{BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip",
            }
            for tag, url in urls.items():
                local = cache_dir / Path(url).name
                if await asyncio.to_thread(local.exists):
                    data = await asyncio.to_thread(local.read_bytes)
                else:
                    fetched = await fetch_month(client, url)
                    if fetched is None:
                        raise FileNotFoundError(f"no archive for {symbol} {m} ({tag})")
                    data = fetched
                    await asyncio.to_thread(local.write_bytes, data)
                if tag == "kl":
                    kl.append(parse_klines_zip(data))
                elif tag == "mk":
                    mk.append(parse_klines_zip(data))
                else:
                    fu.append(parse_funding_zip(data))
    klines = pd.concat(kl).sort_index()
    klines = klines[~klines.index.duplicated()]
    mark = pd.concat(mk).sort_index()
    mark = mark[~mark.index.duplicated()][["high", "low", "close"]]
    mark = mark.rename(columns={"high": "mark_high", "low": "mark_low", "close": "mark_close"})
    funding = pd.concat(fu).sort_index()
    return FuturesFrames(klines=klines, mark=mark, funding=funding)
