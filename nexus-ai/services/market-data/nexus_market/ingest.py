"""REST ingestion: pair metadata sync and closed-candle backfill with gap reporting.

REST is the source of truth for candles (closed bars only). Gaps are reported as
system events and left as gaps; they are never filled with synthetic bars.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Timeframe
from nexus_db import repo, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.errors import UnsupportedFeature
from nexus_exchange.models import Candle
from sqlalchemy.orm import Session, sessionmaker

from nexus_market.analytics import detect_gaps

log = get_logger("nexus.market.ingest")


async def sync_pairs(adapter: ExchangeAdapter, factory: sessionmaker[Session],
                     symbols: list[str]) -> dict[str, int]:
    """Load markets and upsert configured symbols. Returns {symbol: pair_id}."""
    await adapter.load_markets(reload=True)
    now = utcnow()
    synced: dict[str, int] = {}
    missing: list[str] = []
    rules_list = []
    for symbol in symbols:
        try:
            rules_list.append(await adapter.market_rules(symbol))
        except UnsupportedFeature:
            missing.append(symbol)

    def _write() -> None:
        with transaction(factory) as s:
            ex_id = repo.exchange_id(s, adapter.code)
            for r in rules_list:
                synced[r.symbol] = repo.upsert_trading_pair(
                    s, exchange_id=ex_id, symbol=r.symbol, exchange_symbol=r.exchange_symbol,
                    base=r.base, quote=r.quote, price_tick=r.price_tick,
                    amount_step=r.amount_step, min_amount=r.min_amount,
                    min_notional=r.min_notional, maker_fee=r.maker_fee,
                    taker_fee=r.taker_fee, synced_at=now)
            for symbol in missing:
                repo.record_system_event(
                    s, component="market-data", event_type="pair_unavailable",
                    severity="warning", message=f"{symbol} not tradable on {adapter.code}",
                    details={"exchange": adapter.code, "symbol": symbol})

    await asyncio.to_thread(_write)
    return synced


@dataclass
class BackfillResult:
    stored: int = 0
    pages: int = 0
    first: datetime | None = None
    last: datetime | None = None
    gaps: list[tuple[str, str, int]] = field(default_factory=list)


async def backfill(adapter: ExchangeAdapter, factory: sessionmaker[Session], *, pair_id: int,
                   symbol: str, timeframe: Timeframe, start: datetime,
                   now: datetime | None = None, max_pages: int = 500,
                   from_start: bool = False) -> BackfillResult:
    """Fetch closed candles from max(start, last stored + 1 bar) up to now, page by page.
    ``from_start`` re-fetches from ``start`` (history extension; upserts are idempotent)."""
    step = timedelta(seconds=timeframe.seconds)
    now = now or utcnow()
    latest = None if from_start else await asyncio.to_thread(_latest, factory, pair_id,
                                                             timeframe.value)
    cursor = max(start, latest + step) if latest else start
    page_limit = adapter.capabilities.ohlcv_page_limit
    result = BackfillResult()
    seen: list[datetime] = []
    while cursor + step <= now and result.pages < max_pages:
        candles = await adapter.fetch_closed_candles(symbol, timeframe, since=cursor,
                                                     limit=page_limit, now=now)
        result.pages += 1
        fresh = [c for c in candles if c.open_time >= cursor]
        if not fresh:
            break
        await asyncio.to_thread(_store, factory, pair_id, timeframe.value, fresh)
        result.stored += len(fresh)
        seen.extend(c.open_time for c in fresh)
        result.first = result.first or fresh[0].open_time
        result.last = fresh[-1].open_time
        cursor = fresh[-1].open_time + step
    if seen:
        gaps = detect_gaps(seen, timeframe.seconds, start=max(start, min(seen)), end=max(seen))
        result.gaps = [(g.start.isoformat(), g.end.isoformat(), g.missing) for g in gaps]
        if gaps:
            await asyncio.to_thread(_record_gaps, factory, adapter.code, symbol, timeframe,
                                    result.gaps)
    return result


def _latest(factory: sessionmaker[Session], pair_id: int, tf: str) -> datetime | None:
    with transaction(factory) as s:
        return repo.latest_candle_time(s, pair_id, tf)


def _store(factory: sessionmaker[Session], pair_id: int, tf: str, candles: list[Candle]) -> None:
    with transaction(factory) as s:
        repo.upsert_candles(s, pair_id, tf, candles)


def _record_gaps(factory: sessionmaker[Session], exchange: str, symbol: str, tf: Timeframe,
                 gaps: list[tuple[str, str, int]]) -> None:
    missing = sum(g[2] for g in gaps)
    log.warning("candle_gaps", exchange=exchange, symbol=symbol, timeframe=tf.value,
                runs=len(gaps), missing=missing)
    with transaction(factory) as s:
        repo.record_system_event(
            s, component="market-data", event_type="candle_gaps", severity="warning",
            message=f"{missing} missing {tf.value} bars for {symbol} on {exchange}",
            details={"exchange": exchange, "symbol": symbol, "timeframe": tf.value,
                     "gaps": gaps[:50]})
