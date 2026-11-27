"""Bars for NQS-1 from Exness's public tick archive (https://ticks.ex2archive.com).

Exness publishes its own bid/ask ticks per symbol and account type, one zip per month,
with UTC timestamps. This module downloads the Standard-account symbols (``EURUSDm``
and so on), turns each month into M5 bars of the BID price (the MetaTrader convention),
keeps the tick count and the mean and median spread per bar, then resamples H1 and D1
bars (UTC days, like Exness's MT5 server time, which is GMT+0) from the M5 bars.

Nothing is filled in: a 5-minute slot without ticks has no bar. Every file's size and
SHA-256 go in ``provenance.json``; data checks go in ``quality.json``.

Usage: ``python -m nexus_backtest.exness_ticks --out DIR [--workers 4]``
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ARCHIVE = "https://ticks.ex2archive.com/ticks"
SYMBOLS = {"EURUSD": "EURUSDm", "GBPUSD": "GBPUSDm", "USDJPY": "USDJPYm",
           "AUDUSD": "AUDUSDm", "USDCHF": "USDCHFm"}
FIRST, LAST = (2015, 8), (2026, 10)          # complete months in the archive
MAX_ZIP = 400_000_000


def months(first: tuple[int, int] = FIRST, last: tuple[int, int] = LAST
           ) -> list[tuple[int, int]]:
    out, (y, m) = [], first
    while (y, m) <= last:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def url(symbol: str, y: int, m: int) -> str:
    return f"{ARCHIVE}/{symbol}/{y}/{m:02d}/Exness_{symbol}_{y}_{m:02d}.zip"


def download(u: str, retries: int = 4) -> bytes | None:
    """The file's bytes, or None if the archive has no such file (404)."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(u, timeout=120) as r:  # noqa: S310 - fixed https host
                data: bytes = r.read(MAX_ZIP + 1)
            if len(data) > MAX_ZIP:
                raise ValueError(f"{u}: larger than {MAX_ZIP} bytes")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            err: Exception = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            err = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {u}: {err}")


def m5_from_zip(data: bytes) -> tuple[pd.DataFrame, int]:
    """M5 bid bars (open time, UTC) with tick count and spread, from one month's zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in the zip, found {names}")
        with z.open(names[0]) as f:
            ticks = pd.read_csv(f, usecols=["Timestamp", "Bid", "Ask"])
    ticks["Timestamp"] = pd.to_datetime(ticks["Timestamp"], utc=True, format="ISO8601")
    ticks = ticks.dropna()
    ticks = ticks[(ticks["Bid"] > 0) & (ticks["Ask"] >= ticks["Bid"])]
    ticks = ticks.sort_values("Timestamp", kind="stable").set_index("Timestamp")
    spread = ticks["Ask"] - ticks["Bid"]
    g = ticks["Bid"].resample("5min", label="left", closed="left")
    bars = g.ohlc()
    bars["ticks"] = g.count()
    s = spread.resample("5min", label="left", closed="left")
    bars["spread_mean"], bars["spread_median"] = s.mean(), s.median()
    bars = bars[bars["ticks"] > 0]
    return bars, len(ticks)


def resample(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = m5.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                        "low": r["low"].min(), "close": r["close"].last(),
                        "ticks": r["ticks"].sum()})
    return out[out["ticks"] > 0]


def _job(args: tuple[str, str, int, int]) -> dict[str, Any]:
    pair, symbol, y, m = args
    u = url(symbol, y, m)
    data = download(u)
    if data is None:
        return {"pair": pair, "month": f"{y}-{m:02d}", "url": u, "missing": True}
    bars, n = m5_from_zip(data)
    return {"pair": pair, "month": f"{y}-{m:02d}", "url": u, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "ticks": n, "bars": bars}


def quality(pair: str, m5: pd.DataFrame, h1: pd.DataFrame, pip: float) -> dict[str, Any]:
    """Checks fixed in the pre-registration (reported, never used to tune)."""
    gaps = m5.index.to_series().diff()
    long_gaps = gaps[gaps > pd.Timedelta(days=3)]
    # The trading week opens after the weekend gap (Sunday evening UTC); count the
    # (weekday, UTC hour) of each week's first H1 bar, and of each week's last one.
    hgap = h1.index.to_series().diff() > pd.Timedelta(hours=24)
    opens = h1.index[hgap.to_numpy()]
    closes = h1.index[(h1.index.to_series().shift(-1) - h1.index.to_series()
                       > pd.Timedelta(hours=24)).to_numpy()]

    def tally(ts: pd.DatetimeIndex) -> dict[str, int]:
        c = pd.Series([f"{t.day_name()[:3]} {t.hour:02d}:00" for t in ts]).value_counts()
        return {str(k): int(v) for k, v in c.items()}
    sp = m5["spread_median"] / pip
    by_hour = sp.groupby(m5.index.hour).median()
    return {"pair": pair, "m5_bars": len(m5), "h1_bars": len(h1),
            "first": str(m5.index[0]), "last": str(m5.index[-1]),
            "gaps_over_3_days": [str(t) for t in long_gaps.index][:20],
            "week_first_h1_bar_utc": tally(opens),
            "week_last_h1_bar_utc": tally(closes),
            "spread_pips_median": float(np.nanmedian(sp)),
            "spread_pips_median_by_utc_hour": {int(k): round(float(v), 2)
                                               for k, v in by_hour.items()},
            "spread_pips_median_by_year": {int(k): round(float(v), 2) for k, v in
                                           sp.groupby(m5.index.year).median().items()}}


def build(out: Path, workers: int = 4, first: tuple[int, int] = FIRST,
          last: tuple[int, int] = LAST) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(p, s, y, m) for p, s in SYMBOLS.items() for y, m in months(first, last)]
    parts: dict[str, list[pd.DataFrame]] = {p: [] for p in SYMBOLS}
    prov: list[dict[str, Any]] = []
    with ProcessPoolExecutor(workers) as ex:
        for i, res in enumerate(ex.map(_job, jobs), 1):
            bars = res.pop("bars", None)
            if bars is not None:
                parts[res["pair"]].append(bars)
            prov.append(res)
            if i % 50 == 0:
                print(f"{i}/{len(jobs)} months processed", flush=True)
    qual = {}
    for pair, frames in parts.items():
        m5 = pd.concat(frames).sort_index()
        m5 = m5[~m5.index.duplicated(keep="first")]
        h1, d1 = resample(m5, "1h"), resample(m5, "1D")
        for tf, df in (("M5", m5), ("H1", h1), ("D1", d1)):
            df = df.copy()
            df.index = df.index.strftime("%Y-%m-%dT%H:%M:%SZ")
            df.index.name = "time"
            df.to_csv(out / f"{pair}_{tf}.csv", float_format="%.6f")
        qual[pair] = quality(pair, m5, h1, 0.01 if pair.endswith("JPY") else 0.0001)
    meta = {"source": ARCHIVE, "account": "Exness Standard (symbols ending in m)",
            "price": "bid", "timezone": "UTC", "months": [f"{first[0]}-{first[1]:02d}",
                                                          f"{last[0]}-{last[1]:02d}"],
            "built_at": datetime.now(UTC).isoformat(), "files": prov,
            "missing": [f"{p['pair']} {p['month']}" for p in prov if p.get("missing")]}
    (out / "provenance.json").write_text(json.dumps(meta, indent=1))
    (out / "quality.json").write_text(json.dumps(qual, indent=1))
    return {"missing": meta["missing"], "quality": qual}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    res = build(a.out, a.workers)
    print(json.dumps({"missing": res["missing"],
                      "spread_pips_median": {p: q["spread_pips_median"]
                                             for p, q in res["quality"].items()}}))


if __name__ == "__main__":
    main()
