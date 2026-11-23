"""One combined Binance spot WebSocket for all real-time market data NEXUS needs.

Replaces REST polling for live prices and candles:

* ``<symbol>@depth20`` partial book (top 20 levels, every second). Unlike a diff-depth stream
  it needs NO REST snapshot, so reconnecting never spends request weight.
* ``<symbol>@kline_<tf>`` for each stored timeframe: a bar is written when Binance marks it
  closed (``x: true``); REST is then only needed to fill gaps (see runner.poll_candles).

Connection management:

* Heartbeat: Binance pings every ~20 s; aiohttp answers automatically (autoping). Silence
  longer than ``stale_after`` is treated as a dead connection and the stream reconnects.
* Reconnects use exponential back-off with random jitter (2 s doubling to 5 min), at most
  ``max_attempts`` connection attempts per 5 minutes across all workers (Binance allows 300
  per IP), and none while Binance has the IP's REST access paused (same ban).
* State (connected, last message, reconnects, last error) is published to Redis for health
  checks and the System page. Binance closes every connection after 24 h; that is a normal
  reconnect.

Public market data only: no keys, nothing that can place an order.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_exchange.models import Candle

log = get_logger("nexus.market.binance_ws")

STATE_KEY = "nexus:ws:binance:spot"
ATTEMPTS_KEY = "nexus:ws:binance:attempts:{window}"
DEFAULT_BASE = "wss://data-stream.binance.vision"

BookHandler = Callable[[str, list[list[str]], list[list[str]]], Awaitable[None]]
KlineHandler = Callable[[str, str, Candle], Awaitable[None]]


def stream_base(ws_url: str | None) -> str:
    """'wss://host/ws' or 'wss://host' -> 'wss://host' (combined streams live at /stream)."""
    base = (ws_url or DEFAULT_BASE).rstrip("/")
    return base[:-3] if base.endswith("/ws") else base


def native(symbol: str) -> str:
    return symbol.replace("/", "").lower()


@dataclass
class WsState:
    connected: bool = False
    since: str | None = None
    last_message_at: str | None = None
    messages: int = 0
    reconnects: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    next_attempt_at: str | None = None


def parse_kline(data: dict[str, Any]) -> tuple[str, Candle, bool]:
    k = data["k"]
    candle = Candle(datetime.fromtimestamp(int(k["t"]) / 1000, tz=UTC), Decimal(k["o"]),
                    Decimal(k["h"]), Decimal(k["l"]), Decimal(k["c"]), Decimal(k["v"]))
    return str(k["i"]), candle, bool(k["x"])


class BinanceMarketStream:
    def __init__(self, symbols: list[str], timeframes: list[str], redis: Any, *,
                 on_book: BookHandler, on_kline: KlineHandler, ws_url: str | None = None,
                 stale_after: float = 30.0, backoff_min: float = 2.0,
                 backoff_max: float = 300.0, max_attempts: int = 20,
                 connect: Callable[[str], Any] | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 rng: random.Random | None = None) -> None:
        self.symbols, self.timeframes, self.redis = symbols, timeframes, redis
        self.on_book, self.on_kline = on_book, on_kline
        self.base = stream_base(ws_url)
        self.stale_after, self.backoff_min, self.backoff_max = stale_after, backoff_min, backoff_max
        self.max_attempts = max_attempts
        self._connect = connect or self._aiohttp_connect
        self._sleep, self._rng = sleep, rng or random.Random()  # noqa: S311 (jitter only)
        self.state = WsState()
        self._by_native = {native(s): s for s in symbols}
        self._last_state_write = 0.0

    # ------------------------------------------------------------------ setup
    def streams(self) -> list[str]:
        out = []
        for s in self.symbols:
            out.append(f"{native(s)}@depth20")
            out += [f"{native(s)}@kline_{tf}" for tf in self.timeframes]
        return out

    def url(self) -> str:
        return f"{self.base}/stream?streams={'/'.join(self.streams())}"

    def backoff(self, failures: int) -> float:
        base = min(self.backoff_max, self.backoff_min * 2 ** max(0, failures - 1))
        return float(base * self._rng.uniform(0.5, 1.0))   # "equal jitter"

    async def _aiohttp_connect(self, url: str) -> Any:
        import aiohttp
        from nexus_common.proxy import binance_proxy_url

        session = aiohttp.ClientSession(trust_env=True)   # honours HTTPS_PROXY if set
        try:
            ws = await session.ws_connect(url, autoping=True, heartbeat=None,
                                          proxy=binance_proxy_url(),
                                          timeout=aiohttp.ClientWSTimeout(
                                              ws_receive=self.stale_after),
                                          max_msg_size=4 * 1024 * 1024)
        except BaseException:
            await session.close()
            raise
        return _AiohttpConnection(session, ws)

    # ------------------------------------------------------------------ state
    async def _publish_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_state_write < 5:
            return
        self._last_state_write = now
        try:
            await self.redis.set(STATE_KEY, json.dumps(asdict(self.state)), ex=180)
        except Exception as exc:
            log.warning("ws_state_unwritable", error=type(exc).__name__)

    async def _wait_allowed(self, stop: asyncio.Event) -> bool:
        """Wait while Binance has the IP paused or the attempt budget is used. False when
        stopping."""
        from nexus_common.binance_guard import blocked

        while not stop.is_set():
            wait = 0.0
            until = await blocked(self.redis, "spot")
            if until is not None:
                wait = max(1.0, (until - utcnow()).total_seconds())
                reason = f"Binance paused the IP until {until:%H:%M:%S} UTC"
            else:
                window = int(time.time() // 300)
                try:
                    key = ATTEMPTS_KEY.format(window=window)
                    attempts = int(await self.redis.incr(key))
                    await self.redis.expire(key, 600)
                except Exception:
                    attempts = 0          # Redis down: the local back-off still applies
                if attempts <= self.max_attempts:
                    return True
                wait = (window + 1) * 300 - time.time() + 1
                reason = f"connection attempt budget ({self.max_attempts}/5 min) used"
            self.state.next_attempt_at = datetime.fromtimestamp(
                time.time() + wait, tz=UTC).isoformat()
            log.warning("ws_connect_deferred", reason=reason, seconds=round(wait, 1))
            await self._publish_state(force=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except TimeoutError:
                pass
        return False

    # ------------------------------------------------------------------ run
    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not await self._wait_allowed(stop):
                break
            error: str | None = None
            try:
                conn = await self._connect(self.url())
                try:
                    await self._consume(conn, stop)
                finally:
                    await conn.close()
                error = "closed by server" if not stop.is_set() else None
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                error = f"stale: no message in {self.stale_after:.0f}s"
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            if stop.is_set():
                break
            self.state.connected = False
            self.state.consecutive_failures += 1
            self.state.reconnects += 1
            self.state.last_error = error
            delay = self.backoff(self.state.consecutive_failures)
            log.warning("ws_disconnected", stream="binance:spot", error=error,
                        failures=self.state.consecutive_failures, retry_in=round(delay, 1))
            await self._publish_state(force=True)
            await self._sleep(delay)
        self.state.connected = False
        await self._publish_state(force=True)

    async def _consume(self, conn: Any, stop: asyncio.Event) -> None:
        while not stop.is_set():
            raw = await asyncio.wait_for(conn.receive(), timeout=self.stale_after)
            if raw is None:            # closed
                return
            msg = json.loads(raw)
            if not self.state.connected:
                self.state.connected = True
                self.state.since = utcnow().isoformat()
                self.state.consecutive_failures = 0
                self.state.last_error = None
                self.state.next_attempt_at = None
                log.info("ws_connected", stream="binance:spot", streams=len(self.streams()))
                await self._publish_state(force=True)
            self.state.messages += 1
            self.state.last_message_at = utcnow().isoformat()
            await self._dispatch(msg)
            await self._publish_state()

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        stream, data = msg.get("stream", ""), msg.get("data") or {}
        sym = self._by_native.get(stream.split("@", 1)[0])
        if sym is None:
            return
        try:
            if "@depth" in stream:
                await self.on_book(sym, data.get("bids") or [], data.get("asks") or [])
            elif "@kline_" in stream:
                tf, candle, closed = parse_kline(data)
                if closed:
                    await self.on_kline(sym, tf, candle)
        except Exception as exc:   # one bad message must not drop the connection
            log.warning("ws_message_failed", stream=stream, error=f"{type(exc).__name__}: {exc}"[:200])


class _AiohttpConnection:
    def __init__(self, session: Any, ws: Any) -> None:
        self._session, self._ws = session, ws

    async def receive(self) -> str | None:
        import aiohttp

        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            return str(msg.data)
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING):
            return None
        if msg.type == aiohttp.WSMsgType.ERROR:
            raise ConnectionError(str(self._ws.exception()))
        return "{}"                      # binary/ping frames carry no market data

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._session.close()
