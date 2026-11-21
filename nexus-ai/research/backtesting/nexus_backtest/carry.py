"""Funding-rate carry: long spot + short perpetual, held while recent funding is positive.

Pre-registered in docs/research/2026-11-21-carry-preregistration.md. Daily model per unit
of leg notional N (capital 2N): P&L = spot change - perp change + funding received,
rebalanced daily to equal notional; costs on entry, exit and rebalancing turnover.
The position for day t is decided from funding settled before day t starts.
"""

from __future__ import annotations

import asyncio
import csv
import io
import math
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from nexus_backtest.futures_data import BASE as FUT_BASE
from nexus_backtest.futures_data import month_range, parse_funding_zip
from nexus_backtest.futures_validation import _cached

SPOT_BASE = "https://data.binance.vision/data/spot/monthly"
SPOT_COST = 0.0012   # 10 bps fee + 2 bps slippage per side
PERP_COST = 0.0007   # 5 bps fee + 2 bps slippage per side
THRESHOLD = 0.0001   # 0.01% per 8h
LOOKBACK_DAYS = 7


def _ts(raw: str) -> datetime:
    v = int(raw)
    # Binance spot archives switched to microseconds in 2025; perps use milliseconds.
    return datetime.fromtimestamp(v / (1_000_000 if v > 10**14 else 1000), tz=UTC)


def daily_closes_zip(data: bytes) -> pd.Series:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) != 1 or "/" in members[0].filename:
            raise ValueError("unexpected archive layout")
        text = zf.read(members[0]).decode()
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0][:1].isdigit()]
    idx = pd.DatetimeIndex([_ts(r[0]) for r in rows]).floor("D")
    return pd.Series([float(r[4]) for r in rows], index=idx)


async def load(symbol: str, start: str, end: str, cache: Path) -> pd.DataFrame:
    """Columns: spot, perp (daily closes), funding (sum of rates settled that day)."""
    # Spot and perp archives share file names: keep them in separate folders.
    await asyncio.to_thread((cache / "spot").mkdir, parents=True, exist_ok=True)
    spot, perp, fund = [], [], []
    async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
        for m in month_range(start, end):
            spot.append(daily_closes_zip(await _cached(
                client, f"{SPOT_BASE}/klines/{symbol}/1d/{symbol}-1d-{m}.zip", cache / "spot")))
            perp.append(daily_closes_zip(await _cached(
                client, f"{FUT_BASE}/klines/{symbol}/1d/{symbol}-1d-{m}.zip", cache)))
            fund.append(parse_funding_zip(await _cached(
                client, f"{FUT_BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip", cache)))
    s = pd.concat(spot).sort_index()
    p = pd.concat(perp).sort_index()
    f = pd.concat(fund).sort_index().astype(float)
    df = pd.DataFrame({"spot": s[~s.index.duplicated()], "perp": p[~p.index.duplicated()]}).dropna()
    daily_f = f[~f.index.duplicated()].groupby(f.index.floor("D")).sum()
    df["funding"] = daily_f.reindex(df.index, fill_value=0.0)
    return df


def signal(funding: pd.Series, *, threshold: float = THRESHOLD,
           lookback: int = LOOKBACK_DAYS) -> pd.Series:
    """1.0 if in position during day t: mean 8h rate over the previous ``lookback`` days
    (3 settlements per day) is above ``threshold``. Uses only funding before day t."""
    mean_8h = funding.rolling(lookback).sum() / (3 * lookback)
    return (mean_8h > threshold).astype(float).shift(1).fillna(0.0)


def daily_returns(df: pd.DataFrame, position: pd.Series) -> pd.Series:
    """Return on capital (2N) per day."""
    s = df["spot"].pct_change().fillna(0.0)
    f = df["perp"].pct_change().fillna(0.0)
    held = position.reindex(df.index).fillna(0.0)
    gross = held * (s - f + df["funding"])
    rebalance = held * (s.abs() * SPOT_COST + f.abs() * PERP_COST)
    switch = held.diff().abs().fillna(held.abs())  # 1 on entry and on exit
    trade = switch * (SPOT_COST + PERP_COST)
    return (gross - rebalance - trade) / 2.0


def stats(r: pd.Series) -> dict[str, Any]:
    r = r.dropna()
    n = len(r)
    eq = (1 + r).cumprod()
    ann = float(eq.iloc[-1] ** (365 / n) - 1) if n else None
    sharpe = float(r.mean() / r.std() * math.sqrt(365)) if n > 1 and r.std() > 0 else None
    t = float(r.mean() / (r.std() / math.sqrt(n))) if n > 1 and r.std() > 0 else None
    q = (1 + r).groupby(r.index.tz_localize(None).to_period("Q")).prod() - 1
    years = (1 + r).groupby(r.index.year).prod() - 1
    return {"days": n, "annual_return": ann, "sharpe": sharpe, "t_stat": t,
            "max_drawdown": float((1 - eq / eq.cummax()).max()) if n else None,
            "positive_quarters": float((q > 0).mean()) if len(q) else None,
            "quarters": len(q), "total_return": float(eq.iloc[-1] - 1) if n else None,
            "per_year": {str(k): float(v) for k, v in years.items()}}


def evaluate(data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    rule = pd.concat([daily_returns(d, signal(d["funding"])) for d in data.values()],
                     axis=1).dropna().mean(axis=1)
    always = pd.concat([daily_returns(d, pd.Series(1.0, index=d.index)) for d in data.values()],
                       axis=1).dropna().mean(axis=1)
    in_pos = float(np.mean([signal(d["funding"]).mean() for d in data.values()]))
    st = stats(rule)
    failures = []
    if st["annual_return"] is None or st["annual_return"] < 0.04:
        failures.append(f"annual return {st['annual_return']} < 0.04")
    if st["sharpe"] is None or st["sharpe"] < 1.0:
        failures.append(f"Sharpe {st['sharpe']} < 1.0")
    if st["max_drawdown"] is None or st["max_drawdown"] > 0.10:
        failures.append(f"max drawdown {st['max_drawdown']} > 0.10")
    if st["positive_quarters"] is None or st["positive_quarters"] < 0.60:
        failures.append(f"positive quarters {st['positive_quarters']} < 0.60")
    if st["t_stat"] is None or st["t_stat"] < 2.0:
        failures.append(f"t-stat {st['t_stat']} < 2.0")
    return {"rule": st, "always_on": stats(always), "time_in_position": in_pos,
            "passed": not failures, "failures": failures}
