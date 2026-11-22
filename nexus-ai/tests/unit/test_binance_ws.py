"""Combined Binance WebSocket: books and closed candles dispatched, stale and dropped
connections reconnect with jittered back-off, no connecting while Binance has the IP
paused, a connection-attempt budget, and state published for health checks."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import timedelta
from typing import Any

from nexus_common import binance_guard as g
from nexus_common.timeutil import utcnow
from nexus_market.binance_ws import STATE_KEY, BinanceMarketStream, stream_base

from tests.memredis import MemRedis

BOOK = {"stream": "btcusdt@depth20", "data": {"lastUpdateId": 1,
        "bids": [["100.0", "1"], ["99.9", "2"]], "asks": [["100.1", "1"]]}}


def kline(closed: bool, t: int = 1_795_000_000_000) -> dict[str, Any]:
    return {"stream": "btcusdt@kline_1m", "data": {"e": "kline", "k": {
        "t": t, "i": "1m", "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10", "x": closed}}}


class Conn:
    """Scripted connection: items are messages, 'close', 'stale' or an exception."""

    def __init__(self, script: list[Any]) -> None:
        self.script, self.closed = list(script), False

    async def receive(self) -> str | None:
        if not self.script:
            await asyncio.sleep(10)
        item = self.script.pop(0)
        if item == "close":
            return None
        if item == "stale":
            await asyncio.sleep(10)          # longer than stale_after
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    async def close(self) -> None:
        self.closed = True


def make(redis: MemRedis, scripts: list[Any], stop: asyncio.Event, **kw: Any) -> Any:
    books: list[Any] = []
    candles: list[Any] = []
    sleeps: list[float] = []
    urls: list[str] = []

    async def connect(url: str) -> Any:
        urls.append(url)
        if not scripts:
            stop.set()
            raise ConnectionError("no more scripts")
        item = scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        return Conn(item)

    async def on_book(sym: str, bids: Any, asks: Any) -> None:
        books.append((sym, bids[0][0], asks[0][0]))

    async def on_kline(sym: str, tf: str, candle: Any) -> None:
        candles.append((sym, tf, candle.close))

    async def sleep(s: float) -> None:
        sleeps.append(s)

    st = BinanceMarketStream(["BTC/USDT", "ETH/USDT"], ["1m", "5m"], redis, on_book=on_book,
                             on_kline=on_kline, ws_url="wss://data-stream.binance.vision/ws",
                             stale_after=0.05, connect=connect, sleep=sleep,
                             rng=random.Random(7), **kw)
    return st, books, candles, sleeps, urls


def test_streams_and_url() -> None:
    st, *_ = make(MemRedis(), [], asyncio.Event())
    assert st.streams() == ["btcusdt@depth20", "btcusdt@kline_1m", "btcusdt@kline_5m",
                            "ethusdt@depth20", "ethusdt@kline_1m", "ethusdt@kline_5m"]
    assert st.url().startswith("wss://data-stream.binance.vision/stream?streams=btcusdt@depth20/")
    assert stream_base("wss://x.y/ws/") == "wss://x.y"


async def test_dispatch_reconnect_backoff_and_state() -> None:
    r, stop = MemRedis(), asyncio.Event()
    scripts: list[Any] = [
        [BOOK, kline(False), kline(True), "close"],          # server closes (e.g. 24 h)
        ConnectionError("refused"),                          # connect fails
        [BOOK, "stale"],                                     # silence -> reconnect
    ]
    st, books, candles, sleeps, _urls = make(r, scripts, stop)
    await asyncio.wait_for(st.run(stop), timeout=5)
    assert books == [("BTC/USDT", "100.0", "100.1")] * 2
    assert candles == [("BTC/USDT", "1m", __import__("decimal").Decimal("1.5"))]  # closed only
    # Back-off after each failure, jittered (50-100% of base). It grows while failures
    # are consecutive (2 s, then 4 s) and resets once a connection delivers data.
    assert len(sleeps) == 3
    assert 1.0 <= sleeps[0] <= 2.0 and 2.0 <= sleeps[1] <= 4.0 and 1.0 <= sleeps[2] <= 2.0
    state = json.loads(await r.get(STATE_KEY))
    assert state["connected"] is False and state["reconnects"] == 3
    assert "no more scripts" in state["last_error"] or state["last_error"]


async def test_no_connection_attempts_while_binance_has_the_ip_paused() -> None:
    r, stop = MemRedis(), asyncio.Event()
    await r.set(g._key("spot"), (utcnow() + timedelta(seconds=0.2)).isoformat())
    st, _, _, _, urls = make(r, [[BOOK, "close"]], stop)
    task = asyncio.create_task(st.run(stop))
    await asyncio.sleep(0.1)
    assert urls == []                                        # waiting, not connecting
    await asyncio.wait_for(task, timeout=5)
    assert len(urls) >= 1


async def test_connection_attempt_budget_per_five_minutes() -> None:
    r, stop = MemRedis(), asyncio.Event()
    st, _, _, _sleeps, urls = make(r, [ConnectionError("x")] * 10, stop, max_attempts=3)
    task = asyncio.create_task(st.run(stop))
    await asyncio.sleep(0.2)
    assert len(urls) == 3                                    # then it waits for the window
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    state = json.loads(await r.get(STATE_KEY))
    assert state["next_attempt_at"] is not None


async def test_bad_message_does_not_drop_the_connection() -> None:
    r, stop = MemRedis(), asyncio.Event()

    async def bad_book(*_: Any) -> None:
        raise ValueError("boom")

    st, _, candles, _sleeps, _ = make(r, [[BOOK, kline(True), "close"]], stop)
    st.on_book = bad_book
    await asyncio.wait_for(st.run(stop), timeout=5)
    assert len(candles) == 1                                 # processing continued
