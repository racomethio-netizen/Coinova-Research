"""Walk-forward validation of the strategies on Binance USDT-M perpetual futures.

Data: real 5m perpetual klines and funding rates from data.binance.vision (Binance's
public archive). 15m/1h/4h/1d bars are aggregated from 5m with Binance's open-time
convention (a bar covers [open, open + period)), so no bar uses later prices.

Costs: futures taker fee and slippage per side, plus the REAL funding paid over each
trade's holding period (a long pays a positive rate). Returns are per unit of notional:
leverage changes margin use, not this return, and never changes position size.

Strategies are long-only today; shorts are not validated. Results only describe the
past and never enable anything: futures trading stays disabled by configuration.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from nexus_strategies import ALL_STRATEGIES

from nexus_backtest.futures_data import BASE, fetch_month, month_range, parse_funding_zip, parse_klines_zip
from nexus_backtest.metrics import json_safe
from nexus_backtest.signals import CostModel
from nexus_backtest.walkforward import walk_forward

RULES = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
FUTURES_COSTS = CostModel(fee_rate=0.0005, slippage_rate=0.0002)


@dataclass(frozen=True)
class SymbolData:
    frames: dict[str, pd.DataFrame]
    funding: pd.Series  # float rates indexed by funding time (UTC)


def resample(five: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {"5m": five}
    for tf, rule in RULES.items():
        agg = five.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        out[tf] = agg.dropna(subset=["open"])
    return out


async def _cached(client: httpx.AsyncClient, url: str, cache_dir: Path) -> bytes:
    local = cache_dir / Path(url).name
    if await asyncio.to_thread(local.exists):
        return await asyncio.to_thread(local.read_bytes)
    data = await fetch_month(client, url)
    if data is None:
        raise FileNotFoundError(f"archive not found: {url}")
    await asyncio.to_thread(local.write_bytes, data)
    return data


async def load_symbol(symbol: str, start: str, end: str, cache_dir: Path) -> SymbolData:
    """Months YYYY-MM inclusive. Missing archive months raise; nothing is filled in."""
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    kl, fu = [], []
    async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
        for m in month_range(start, end):
            kl.append(parse_klines_zip(await _cached(
                client, f"{BASE}/klines/{symbol}/5m/{symbol}-5m-{m}.zip", cache_dir)))
            fu.append(parse_funding_zip(await _cached(
                client, f"{BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip", cache_dir)))
    five = pd.concat(kl).sort_index()
    five = five[~five.index.duplicated()].astype(float)
    funding = pd.concat(fu).sort_index().astype(float)
    return SymbolData(resample(five), funding[~funding.index.duplicated()])


def funding_adjuster(funding: dict[str, pd.Series]) -> Any:
    """Subtract the funding rates settled while each (long) trade was open."""
    def adjust(study: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if study.empty:
            return study.assign(funding=0.0)
        rates = funding[symbol]
        times = rates.index
        cum = rates.cumsum().to_numpy()
        lo = times.searchsorted(study.entry_time.to_numpy(), side="right")
        hi = times.searchsorted(study.exit_time.to_numpy(), side="right")
        paid = [float(cum[b - 1] - (cum[a - 1] if a > 0 else 0.0)) if b > a else 0.0
                for a, b in zip(lo, hi, strict=True)]
        out = study.assign(funding=paid)
        out["ret"] = out["ret"] - out["funding"]
        out["win"] = out["ret"] > 0
        return out
    return adjust


def validate(data: dict[str, SymbolData], *, train_days: int = 180,
             test_days: int = 60) -> list[dict[str, Any]]:
    frames = {sym: d.frames for sym, d in data.items()}
    adjust = funding_adjuster({sym: d.funding for sym, d in data.items()})
    results = []
    for code, cls in ALL_STRATEGIES.items():
        rep = walk_forward(frames, cls, costs=FUTURES_COSTS, adjust=adjust,
                           train_days=train_days, test_days=test_days)
        windows = [{**w, "train_start": str(w["train_start"]), "test_start": str(w["test_start"]),
                    "test_end": str(w["test_end"])} for w in rep.windows]
        funding_paid = float(rep.oos["funding"].sum()) if "funding" in rep.oos else 0.0
        results.append({"strategy": code, "passed": rep.passed, "failures": rep.failures,
                        "summary": json_safe(rep.stats), "windows": json_safe(windows),
                        "oos_funding_paid": funding_paid})
    return results


def record(factory: Any, results: list[dict[str, Any]], *, symbols: list[str],
           data_start: datetime, data_end: datetime) -> None:
    from nexus_db import models, transaction
    from sqlalchemy import select

    with transaction(factory) as s:
        for r in results:
            strat = s.scalar(select(models.Strategy).where(models.Strategy.code == r["strategy"]))
            s.add(models.BacktestRun(
                strategy_id=strat.id if strat else None, status="succeeded", is_walk_forward=True,
                config={"market": "futures", "exchange": "binance-usdm", "symbols": symbols,
                        "train_days": 180, "test_days": 60, "side": "long-only",
                        "funding": "real archive rates deducted",
                        "source": "data.binance.vision"},
                data_start=data_start, data_end=data_end,
                fee_bps=FUTURES_COSTS.fee_rate * 10_000,
                slippage_bps=FUTURES_COSTS.slippage_rate * 10_000,
                metrics={"summary": r["summary"], "windows": r["windows"], "passed": r["passed"],
                         "failures": r["failures"], "oos_funding_paid": r["oos_funding_paid"]},
                completed_at=datetime.now(UTC)))
