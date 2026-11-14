"""Market-data service process: ``python -m nexus_market.runner``.

1. Sync pair metadata for every enabled exchange.
2. Backfill closed candles for every pair/timeframe.
3. Loop: incremental candle polling (publishes closed-candle events), periodic order
   book snapshots to Postgres, exchange health checks, and WebSocket top-of-book
   streams cached in Redis (short TTL, so stale prices disappear on their own).
"""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import timedelta
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis
from nexus_common import events
from nexus_common.logging import configure_logging, get_logger
from nexus_common.timeutil import utcnow
from nexus_config import ExchangeCode, Settings, get_settings
from nexus_db import make_engine, make_session_factory, models, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.errors import ExchangeError
from nexus_exchange.factory import build_from_settings, build_ws_client
from nexus_exchange.models import OrderBook
from sqlalchemy.orm import Session, sessionmaker

from nexus_market.analytics import book_metrics
from nexus_market.health import HealthMonitor
from nexus_market.ingest import backfill, sync_pairs
from nexus_market.stream import ReconnectingStream

log = get_logger("nexus.market")
BOOK_TTL_SECONDS = 20


class MarketDataService:
    def __init__(self, settings: Settings, factory: sessionmaker[Session], redis: Any) -> None:
        self.settings, self.factory, self.redis = settings, factory, redis
        self.adapters: dict[str, ExchangeAdapter] = {
            e.value: build_from_settings(e, settings) for e in settings.enabled_exchanges}
        self.pairs: dict[tuple[str, str], int] = {}
        self.stop = asyncio.Event()

    async def bootstrap(self) -> None:
        for code, adapter in self.adapters.items():
            try:
                synced = await sync_pairs(adapter, self.factory, self.settings.trading_pairs)
            except ExchangeError as exc:
                log.error("pair_sync_failed", exchange=code, error=str(exc))
                continue
            for symbol, pid in synced.items():
                self.pairs[(code, symbol)] = pid
        log.info("pairs_synced", count=len(self.pairs))

    async def poll_candles(self, *, initial: bool = False) -> int:
        stored = 0
        start = utcnow() - timedelta(days=self.settings.market_backfill_days)
        for (code, symbol), pid in self.pairs.items():
            for tf in self.settings.timeframes:
                try:
                    res = await backfill(self.adapters[code], self.factory, pair_id=pid,
                                         symbol=symbol, timeframe=tf, start=start)
                except ExchangeError as exc:
                    log.warning("candle_poll_failed", exchange=code, symbol=symbol,
                                timeframe=tf.value, error=str(exc)[:200])
                    continue
                stored += res.stored
                if res.stored and not initial and res.last is not None:
                    await self.redis.xadd(events.CANDLE_STREAM, events.encode({
                        "exchange": code, "symbol": symbol, "timeframe": tf.value,
                        "open_time": res.last.isoformat()}),
                        maxlen=events.STREAM_MAXLEN, approximate=True)
                if initial:
                    log.info("backfilled", exchange=code, symbol=symbol, timeframe=tf.value,
                             bars=res.stored, gaps=len(res.gaps))
        return stored

    async def snapshot_books(self) -> None:
        for (code, symbol), pid in self.pairs.items():
            try:
                book = await self.adapters[code].fetch_order_book(symbol, depth=50)
            except ExchangeError as exc:
                log.warning("book_snapshot_failed", exchange=code, symbol=symbol, error=str(exc))
                continue
            metrics = book_metrics(book)
            if metrics is None:
                continue

            def _save(book: OrderBook = book, pid: int = pid, metrics: Any = metrics) -> None:
                with transaction(self.factory) as s:
                    s.add(models.OrderbookSnapshot(
                        pair_id=pid, captured_at=book.timestamp, best_bid=metrics.best_bid,
                        best_ask=metrics.best_ask, spread_bps=metrics.spread_bps,
                        bid_depth_quote=metrics.bid_depth_quote,
                        ask_depth_quote=metrics.ask_depth_quote,
                        bids=[[str(p), str(a)] for p, a in book.bids],
                        asks=[[str(p), str(a)] for p, a in book.asks]))

            await asyncio.to_thread(_save)

    async def publish_book(self, code: str, symbol: str, raw: dict[str, Any]) -> None:
        bids, asks = raw.get("bids") or [], raw.get("asks") or []
        if not bids or not asks:
            return
        bid, ask = Decimal(repr(bids[0][0])), Decimal(repr(asks[0][0]))
        if bid >= ask:
            log.warning("crossed_ws_book", exchange=code, symbol=symbol)
            return
        await self.redis.set(events.book_key(code, symbol), json.dumps({
            "bid": str(bid), "ask": str(ask), "bid_size": str(bids[0][1]),
            "ask_size": str(asks[0][1]), "ts": utcnow().isoformat(), "source": "ws"}),
            ex=BOOK_TTL_SECONDS)

    def ws_streams(self) -> list[tuple[ReconnectingStream[Any], Any]]:
        out = []
        for code in self.adapters:
            client = build_ws_client(ExchangeCode(code), self.settings)
            for (c, symbol) in self.pairs:
                if c != code:
                    continue

                async def watch(client: Any = client, symbol: str = symbol) -> Any:
                    return await client.watch_order_book(symbol, 10)

                async def on_update(raw: Any, code: str = code, symbol: str = symbol) -> None:
                    await self.publish_book(code, symbol, raw)

                async def on_state(name: str, connected: bool, error: str | None) -> None:
                    if not connected:
                        log.warning("ws_disconnected", stream=name, error=error)

                out.append((ReconnectingStream(f"{code}:{symbol}:book", watch, on_update,
                                               on_state=on_state), client))
        return out

    async def _every(self, seconds: float, fn: Any, name: str) -> None:
        while not self.stop.is_set():
            try:
                await fn()
            except Exception as exc:  # a loop must survive any single failure; log it
                log.error("loop_error", loop=name, error=f"{type(exc).__name__}: {exc}"[:300])
            await self.redis.set(events.heartbeat_key(f"market-data:{name}"),
                                 utcnow().isoformat(), ex=int(seconds * 4) + 30)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=seconds)
            except TimeoutError:
                pass

    async def run(self) -> None:
        await self.bootstrap()
        total = await self.poll_candles(initial=True)
        log.info("backfill_complete", bars=total)
        monitor = HealthMonitor(list(self.adapters.values()), self.factory, self.redis)
        streams = self.ws_streams()
        tasks = [
            asyncio.create_task(self._every(30, self.poll_candles, "candles")),
            asyncio.create_task(self._every(300, self.snapshot_books, "books")),
            asyncio.create_task(self._every(60, monitor.check_once, "health")),
            *(asyncio.create_task(s.run(self.stop)) for s, _ in streams),
        ]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _, client in streams:
            await client.close()
        for adapter in self.adapters.values():
            await adapter.close()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = make_engine(settings.database_url.get_secret_value())
    redis = aioredis.from_url(settings.redis_url.get_secret_value())  # type: ignore[no-untyped-call]
    service = MarketDataService(settings, make_session_factory(engine), redis)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.stop.set)
    try:
        await service.run()
    finally:
        await redis.aclose()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
