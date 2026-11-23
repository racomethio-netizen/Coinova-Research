"""Market-data service process: ``python -m nexus_market.runner``.

1. Sync pair metadata for every enabled exchange.
2. Backfill closed candles for every pair/timeframe (REST, only bars that are missing).
3. Loop: one combined Binance WebSocket delivers order books (top 20 levels) and closed
   candles in real time; REST is used only to fill candle gaps (a bar that is overdue and
   did not arrive on the stream). Every REST call goes through the central rate-limit gate
   (nexus_common.binance_guard). Order book snapshots for Postgres come from the stream.
   Venue health is passive (stream freshness and the gate), not a ping loop.
"""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import timedelta
from decimal import Decimal
from typing import Any

from nexus_common import events
from nexus_common.logging import configure_logging, get_logger
from nexus_common.redisutil import connect as connect_redis
from nexus_common.timeutil import utcnow
from nexus_config import ExchangeCode, Settings, get_settings
from nexus_db import make_engine, make_session_factory, models, transaction
from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
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
MINUTE_PAGES_PER_POLL = 3     # up to 1,500 one-minute bars per pair per poll
# While the WebSocket is live, a closed bar is expected from the stream; REST only fetches
# bars still missing this long after they closed.
WS_GRACE = timedelta(seconds=90)
WS_FRESH_SECONDS = 30


class MarketDataService:
    def __init__(self, settings: Settings, factory: sessionmaker[Session], redis: Any) -> None:
        self.settings, self.factory, self.redis = settings, factory, redis
        self.adapters: dict[str, ExchangeAdapter] = {
            e.value: build_from_settings(e, settings) for e in settings.enabled_exchanges}
        from nexus_common.binance_guard import attach_weight_hooks

        attach_weight_hooks(self.adapters, redis)
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
        # Pairs already known from an earlier start: usable without any REST call (e.g. when
        # Binance has the IP paused at start-up, the live stream still starts).
        from nexus_db import repo

        with transaction(self.factory) as s:
            for code in self.adapters:
                for symbol in self.settings.trading_pairs:
                    if (code, symbol) in self.pairs:
                        continue
                    try:
                        self.pairs[(code, symbol)] = repo.pair_id(s, code, symbol)
                        log.info("pair_from_database", exchange=code, symbol=symbol)
                    except LookupError:
                        continue
        log.info("pairs_synced", count=len(self.pairs))

    async def _paused(self) -> bool:
        """True while Binance asked us to back off (shared with every other caller)."""
        from nexus_common.binance_guard import blocked

        until = await blocked(self.redis, "spot")
        if until is not None:
            log.info("binance_backoff_active", until=until.isoformat())
        return until is not None

    async def _note(self, exc: BaseException) -> None:
        from nexus_common.binance_guard import note_exception

        until = await note_exception(self.redis, "spot", exc)
        if until is not None:
            log.warning("binance_backoff_recorded", until=until.isoformat())

    async def poll_candles(self, *, initial: bool = False) -> int:
        stored = 0
        if await self._paused():
            return 0
        start = utcnow() - timedelta(days=self.settings.market_backfill_days)
        horizon = await self._rest_horizon()
        for (code, symbol), pid in self.pairs.items():
            for tf in self.settings.timeframes:
                try:
                    res = await backfill(self.adapters[code], self.factory, pair_id=pid,
                                         symbol=symbol, timeframe=tf, start=start,
                                         now=horizon)
                except ExchangeError as exc:
                    log.warning("candle_poll_failed", exchange=code, symbol=symbol,
                                timeframe=tf.value, error=str(exc)[:200])
                    await self._note(exc)
                    if await self._paused():
                        return stored
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
        if self.settings.minute_candles:
            stored += await self.poll_minutes()
        if stored > 500:                       # a history page or more was parsed
            from nexus_common.memory import release

            release()
        return stored

    async def poll_minutes(self) -> int:
        """1m candles for the traded pairs, kept ``minute_retention_days``. History is
        filled a few pages per poll (not in one burst: the egress IP is shared)."""
        from nexus_config import Timeframe

        stored = 0
        start = utcnow() - timedelta(days=self.settings.minute_retention_days)
        horizon = await self._rest_horizon()
        for (code, symbol), pid in self.pairs.items():
            if symbol not in self.settings.trading_pairs or await self._paused():
                continue
            try:
                res = await backfill(self.adapters[code], self.factory, pair_id=pid,
                                     symbol=symbol, timeframe=Timeframe.M1, start=start,
                                     max_pages=MINUTE_PAGES_PER_POLL, now=horizon)
            except ExchangeError as exc:
                log.warning("candle_poll_failed", exchange=code, symbol=symbol,
                            timeframe="1m", error=str(exc)[:200])
                await self._note(exc)
                continue
            stored += res.stored
        return stored

    async def repair_gaps(self) -> int:
        """Fill holes in stored history (the stream writes the newest bar, so the regular
        poll never looks back). A Redis cursor per series moves past each repaired span, so
        bars Binance itself never had (exchange outages) are not requested again and again;
        it resets every 6 hours to re-scan from the start of the window."""
        from nexus_config import Timeframe
        from nexus_db import repo

        series = [(tf, self.settings.daily_history_days if tf is Timeframe.D1
                   else self.settings.market_backfill_days) for tf in self.settings.timeframes]
        if self.settings.minute_candles:
            series.insert(0, (Timeframe.M1, self.settings.minute_retention_days))
        horizon = await self._rest_horizon()
        filled = 0
        for (code, symbol), pid in self.pairs.items():
            for tf, days in series:
                if await self._paused():
                    return filled
                key = f"nexus:md:gapcursor:{pid}:{tf.value}:{days}"   # new window, new scan
                raw = await self.redis.get(key)
                start = utcnow() - timedelta(days=days)
                if raw:
                    from datetime import datetime as _dt

                    start = max(start, _dt.fromisoformat(raw.decode() if isinstance(raw, bytes)
                                                         else raw))

                def find(start: Any = start, tf: Any = tf, pid: int = pid) -> Any:
                    with transaction(self.factory) as s:
                        return repo.first_gap(s, pid, tf.value, start, horizon, tf.seconds)

                gap = await asyncio.to_thread(find)
                if gap is None:
                    await self.redis.set(key, horizon.isoformat(), ex=6 * 3600)
                    continue
                try:
                    res = await backfill(self.adapters[code], self.factory, pair_id=pid,
                                         symbol=symbol, timeframe=tf, start=gap, now=horizon,
                                         max_pages=MINUTE_PAGES_PER_POLL, from_start=True)
                except ExchangeError as exc:
                    log.warning("gap_repair_failed", symbol=symbol, timeframe=tf.value,
                                error=str(exc)[:200])
                    await self._note(exc)
                    continue
                filled += res.stored
                span = timedelta(seconds=tf.seconds * self.adapters[code].capabilities
                                 .ohlcv_page_limit * MINUTE_PAGES_PER_POLL)
                await self.redis.set(key, min(gap + span, horizon).isoformat(), ex=6 * 3600)
                log.info("gap_repaired", symbol=symbol, timeframe=tf.value,
                         from_=gap.isoformat(), bars=res.stored)
        if filled > 500:
            from nexus_common.memory import release

            release()
        return filled

    async def ws_fresh(self) -> bool:
        """True while the Binance stream is connected and delivered data recently."""
        from nexus_market.binance_ws import STATE_KEY

        try:
            raw = await self.redis.get(STATE_KEY)
        except Exception:
            return False
        if not raw:
            return False
        st = json.loads(raw)
        last = st.get("last_message_at")
        if not st.get("connected") or not last:
            return False
        from datetime import datetime

        return (utcnow() - datetime.fromisoformat(last)).total_seconds() < WS_FRESH_SECONDS

    async def passive_health(self, code: str) -> Any:
        """Binance health without a ping: the live stream, else the rate-limit gate.
        None = unknown (the monitor falls back to an active check, which is gated too)."""
        if code != "binance":
            return None
        from nexus_common.binance_guard import blocked
        from nexus_exchange.models import HealthStatus

        until = await blocked(self.redis, "spot")
        if await self.ws_fresh():
            note = (f"REST paused by Binance until {until:%H:%M} UTC; live stream OK"
                    if until else None)
            return HealthStatus(code, True, None, utcnow(), note)
        if until is not None:
            return HealthStatus(code, False, None, utcnow(),
                                f"Binance paused this server's IP until {until:%H:%M} UTC "
                                "(request-weight limit on the shared hosting IP)")
        return None

    async def _rest_horizon(self) -> Any:
        """'Now' for REST gap filling: with a live stream, bars closed in the last
        WS_GRACE are left to the stream (no duplicate REST request)."""
        now = utcnow()
        return now - WS_GRACE if await self.ws_fresh() else now

    async def store_ws_candle(self, symbol: str, tf: str, candle: Any) -> None:
        pid = self.pairs.get(("binance", symbol))
        if pid is None:
            return
        from nexus_db import repo

        def save() -> None:
            with transaction(self.factory) as s:
                repo.upsert_candles(s, pid, tf, [candle], source="ws")

        await asyncio.to_thread(save)
        await self.redis.xadd(events.CANDLE_STREAM, events.encode({
            "exchange": "binance", "symbol": symbol, "timeframe": tf,
            "open_time": candle.open_time.isoformat()}),
            maxlen=events.STREAM_MAXLEN, approximate=True)

    async def derive_timeframes(self) -> int:
        """3m/30m/2h/6h/12h from stored closed bars (no exchange calls)."""
        from nexus_config import DERIVED_TIMEFRAMES

        from nexus_market.derived import derive

        targets = [t for t in DERIVED_TIMEFRAMES
                   if self.settings.minute_candles or DERIVED_TIMEFRAMES[t] != "1m"]

        def run() -> int:
            n = 0
            for pid in self.pairs.values():
                for target in targets:
                    with transaction(self.factory) as s:
                        n += derive(s, pid, target)
            return n

        n = await asyncio.to_thread(run)
        if n:
            from nexus_common.memory import release

            release()
        return n

    async def prune_minutes(self) -> None:
        from nexus_market.derived import prune_minutes

        def run() -> int:
            with transaction(self.factory) as s:
                return prune_minutes(s, self.settings.minute_retention_days)

        removed = await asyncio.to_thread(run)
        if removed:
            log.info("minute_candles_pruned", rows=removed)

    async def snapshot_books(self) -> None:
        for (code, symbol), pid in self.pairs.items():
            book = await read_ws_book(self.redis, code, symbol, max_age=10)
            if book is None:
                if await self._paused():
                    return
            try:
                book = book or await self.adapters[code].fetch_order_book(symbol, depth=50)
            except ExchangeError as exc:
                log.warning("book_snapshot_failed", exchange=code, symbol=symbol, error=str(exc))
                await self._note(exc)
                if await self._paused():
                    return
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
        levels_b = [[str(p), str(q)] for p, q, *_ in bids[:20]]
        levels_a = [[str(p), str(q)] for p, q, *_ in asks[:20]]
        bid, ask = Decimal(levels_b[0][0]), Decimal(levels_a[0][0])
        if bid >= ask:
            log.warning("crossed_ws_book", exchange=code, symbol=symbol)
            return
        await self.redis.set(events.book_key(code, symbol), json.dumps({
            "bid": str(bid), "ask": str(ask), "bid_size": levels_b[0][1],
            "ask_size": levels_a[0][1], "ts": utcnow().isoformat(), "source": "ws",
            "bids": levels_b, "asks": levels_a}), ex=BOOK_TTL_SECONDS)

    def ws_streams(self) -> list[tuple[Any, Any]]:
        out: list[tuple[Any, Any]] = []
        for code, adapter in self.adapters.items():
            if code == "binance":
                out.append((self.binance_stream(), None))
                continue
            seed = adapter.ccxt_markets() if isinstance(adapter, CcxtSpotAdapter) else None
            client = build_ws_client(ExchangeCode(code), self.settings, markets=seed)
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

    def binance_stream(self) -> Any:
        from nexus_market.binance_ws import BinanceMarketStream

        tfs = [tf.value for tf in self.settings.timeframes]
        if self.settings.minute_candles and "1m" not in tfs:
            tfs.insert(0, "1m")
        symbols = [s for (c, s) in self.pairs if c == "binance"]

        async def on_book(symbol: str, bids: list[list[str]], asks: list[list[str]]) -> None:
            await self.publish_book("binance", symbol, {"bids": bids, "asks": asks})

        return BinanceMarketStream(symbols, tfs, self.redis, on_book=on_book,
                                   on_kline=self.store_ws_candle,
                                   ws_url=self.settings.binance_public_ws_url)

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
        monitor = HealthMonitor(list(self.adapters.values()), self.factory, self.redis,
                                passive=self.passive_health)
        streams = self.ws_streams()
        tasks = [
            asyncio.create_task(self._every(30, self.poll_candles, "candles")),
            asyncio.create_task(self._every(300, self.snapshot_books, "books")),
            asyncio.create_task(self._every(60, monitor.check_once, "health")),
            asyncio.create_task(self._every(60, self.derive_timeframes, "derived")),
            asyncio.create_task(self._every(300, self.repair_gaps, "gap-repair")),
            asyncio.create_task(self._every(3600, self.prune_minutes, "retention")),
            *(asyncio.create_task(s.run(self.stop)) for s, _ in streams),
        ]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _, client in streams:
            if client is not None:
                await client.close()
        for adapter in self.adapters.values():
            await adapter.close()


async def read_ws_book(redis: Any, code: str, symbol: str, *,
                       max_age: float) -> OrderBook | None:
    """The stream's order book from Redis (top 20 levels) if it is fresh enough."""
    from datetime import datetime

    try:
        raw = await redis.get(events.book_key(code, symbol))
    except Exception:
        return None
    if not raw:
        return None
    d = json.loads(raw)
    if not d.get("bids") or not d.get("asks"):
        return None
    ts = datetime.fromisoformat(d["ts"])
    if (utcnow() - ts).total_seconds() > max_age:
        return None
    return OrderBook(symbol, tuple((Decimal(p), Decimal(q)) for p, q in d["bids"]),
                     tuple((Decimal(p), Decimal(q)) for p, q in d["asks"]), ts)


async def _relay(src: asyncio.Event, dst: asyncio.Event) -> None:
    await src.wait()
    dst.set()


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set (used by main() and by the API's inline worker mode)."""
    engine = make_engine(settings.database_url.get_secret_value(), pool_size=3, max_overflow=2)
    redis = connect_redis(settings.redis_url.get_secret_value())
    from nexus_common.binance_guard import set_default_redis

    set_default_redis(redis)
    service = MarketDataService(settings, make_session_factory(engine), redis)
    relay = asyncio.create_task(_relay(stop, service.stop))
    try:
        await service.run()
    finally:
        relay.cancel()
        await redis.aclose()
        engine.dispose()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
