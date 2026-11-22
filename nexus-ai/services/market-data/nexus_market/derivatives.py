"""Public Binance USD-M futures context: funding, basis, open interest (+history),
long/short ratios and liquidations. Read-only, no API key.

Every field is either measured or None with a reason; nothing is estimated. Some
locations are refused by Binance (HTTP 451 / "restricted location"); that is reported,
never worked around. Ratios are labelled with what they measure: account ratios count
accounts, not size, and are a weak crowding signal.

Liquidations come from the public ``!forceOrder@arr`` stream (Binance sends at most the
latest liquidation per symbol per second, so totals are a LOWER BOUND; this is stated).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx
from nexus_common.binance_guard import BinanceBackoff
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

log = get_logger("nexus.market.derivatives")

FAPI = "https://fapi.binance.com"
FORCE_STREAM = "wss://fstream.binance.com/ws/!forceOrder@arr"
KEY = "nexus:derivatives:{symbol}"
LIQ_KEY = "nexus:liquidations:{symbol}"
TTL = 20 * 60


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _pct_change(new: float | None, old: float | None) -> float | None:
    return None if new is None or not old else new / old - 1


def _restricted(r: httpx.Response) -> bool:
    return r.status_code == 451 or "restricted location" in r.text[:300].lower()


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any],
               redis: Any = None) -> Any:
    from nexus_common.binance_guard import guarded_get

    r = await guarded_get(client, redis, f"{FAPI}{path}", params)
    if _restricted(r):
        raise PermissionError("Binance refuses this server's location for futures data")
    r.raise_for_status()
    return r.json()


def parse_snapshot(native: str, premium: dict[str, Any], oi_now: dict[str, Any],
                   oi_hist: list[dict[str, Any]], accounts: list[dict[str, Any]],
                   top: list[dict[str, Any]], taker: list[dict[str, Any]]) -> dict[str, Any]:
    mark, index = _f(premium.get("markPrice")), _f(premium.get("indexPrice"))
    hist = sorted(oi_hist, key=lambda r: r.get("timestamp") or 0)
    oi_values = [_f(r.get("sumOpenInterestValue")) for r in hist]
    last_val = oi_values[-1] if oi_values else None

    def back(n: int) -> float | None:
        return oi_values[-1 - n] if len(oi_values) > n else None

    def latest(rows: list[dict[str, Any]], field: str) -> float | None:
        rows = sorted(rows, key=lambda r: r.get("timestamp") or 0)
        return _f(rows[-1].get(field)) if rows else None

    acc = sorted(accounts, key=lambda r: r.get("timestamp") or 0)
    return {
        "symbol": native, "as_of": utcnow().isoformat(),
        "funding_rate": _f(premium.get("lastFundingRate")),
        "next_funding_time": premium.get("nextFundingTime"),
        "mark_price": mark, "index_price": index,
        "basis": None if mark is None or not index else mark / index - 1,
        "open_interest": _f(oi_now.get("openInterest")),
        "open_interest_value": last_val,
        "oi_change_1h": _pct_change(last_val, back(1)),
        "oi_change_24h": _pct_change(last_val, back(24)),
        "accounts_long_short_ratio": latest(accounts, "longShortRatio"),
        "accounts_long_short_ratio_24h_ago": _f(acc[0].get("longShortRatio")) if len(
            acc) > 24 else None,
        "top_traders_position_ratio": latest(top, "longShortRatio"),
        "taker_buy_sell_ratio": latest(taker, "buySellRatio"),
        "notes": ["account ratio counts accounts, not size (weak signal)",
                  "top-trader ratio is Binance's definition of its largest accounts"],
    }


async def fetch_snapshot(client: httpx.AsyncClient, symbol: str,
                         redis: Any = None) -> dict[str, Any]:
    native = symbol.replace("/", "")
    hp = {"symbol": native, "period": "1h", "limit": 25}
    premium, oi_now, oi_hist, acc, top, taker = await asyncio.gather(
        _get(client, "/fapi/v1/premiumIndex", {"symbol": native}, redis),
        _get(client, "/fapi/v1/openInterest", {"symbol": native}, redis),
        _get(client, "/futures/data/openInterestHist", hp, redis),
        _get(client, "/futures/data/globalLongShortAccountRatio", hp, redis),
        _get(client, "/futures/data/topLongShortPositionRatio", hp, redis),
        _get(client, "/futures/data/takerlongshortRatio", hp, redis))
    return parse_snapshot(native, premium, oi_now, oi_hist, acc, top, taker)


# ------------------------------------------------------------------ liquidations
class LiquidationTracker:
    """Per-symbol, per-minute notional buckets of liquidations, by the side that was
    liquidated (an order with side SELL closes a LONG position)."""

    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols
        self.buckets: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
        self.last_event: dict[str, str] = {}

    def add(self, event: dict[str, Any]) -> bool:
        o = event.get("o") or {}
        sym = o.get("s")
        if sym not in self.symbols:
            return False
        qty, price = _f(o.get("z") or o.get("q")), _f(o.get("ap") or o.get("p"))
        if not qty or not price:
            return False
        ts = int(o.get("T") or event.get("E") or time.time() * 1000)
        minute = ts // 60_000
        side = "longs_liquidated" if o.get("S") == "SELL" else "shorts_liquidated"
        b = self.buckets[sym].setdefault(minute, {"longs_liquidated": 0.0,
                                                  "shorts_liquidated": 0.0})
        b[side] += qty * price
        self.last_event[sym] = datetime.fromtimestamp(ts / 1000, tz=UTC).isoformat()
        return True

    def summary(self, symbol: str, now_ms: int | None = None) -> dict[str, Any]:
        now_min = (now_ms or int(time.time() * 1000)) // 60_000
        b = self.buckets.get(symbol, {})
        for m in [m for m in b if m < now_min - 1440]:      # keep 24 h
            del b[m]

        def total(minutes: int, side: str) -> float:
            return round(sum(v[side] for m, v in b.items() if m > now_min - minutes), 2)

        return {"symbol": symbol, "longs_liquidated_1h": total(60, "longs_liquidated"),
                "shorts_liquidated_1h": total(60, "shorts_liquidated"),
                "longs_liquidated_24h": total(1440, "longs_liquidated"),
                "shorts_liquidated_24h": total(1440, "shorts_liquidated"),
                "last_event": self.last_event.get(symbol),
                "note": "USDT notional from Binance's public stream; lower bound (at most "
                        "one event per symbol per second is published)"}


async def liquidation_stream(tracker: LiquidationTracker, redis: Any, stop: asyncio.Event,
                             url: str = FORCE_STREAM) -> None:
    import websockets

    delay = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=60, open_timeout=15) as ws:
                delay = 1.0
                await redis.set("nexus:liquidations:status", json.dumps(
                    {"connected": True, "at": utcnow().isoformat()}), ex=TTL)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    for ev in msg if isinstance(msg, list) else [msg]:
                        tracker.add(ev)
        except Exception as exc:  # reconnect with capped exponential backoff
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.warning("liquidation_stream_down", error=reason, retry_in=delay)
            await redis.set("nexus:liquidations:status", json.dumps(
                {"connected": False, "error": reason, "at": utcnow().isoformat()}), ex=TTL)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 300)


async def serve(settings: Any, stop: asyncio.Event, *, interval: float = 300) -> None:
    from nexus_common.redisutil import connect as connect_redis

    redis = connect_redis(settings.redis_url.get_secret_value())
    natives = {p.replace("/", "") for p in settings.trading_pairs}
    tracker = LiquidationTracker(natives)
    stream = asyncio.create_task(liquidation_stream(tracker, redis, stop))
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=True) as client:
            while not stop.is_set():
                for pair in settings.trading_pairs:
                    native = pair.replace("/", "")
                    try:
                        snap = await fetch_snapshot(client, pair, redis)
                    except (httpx.HTTPError, PermissionError, ValueError, BinanceBackoff) as exc:
                        snap = {"symbol": native, "as_of": utcnow().isoformat(),
                                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                        log.warning("derivatives_fetch_failed", symbol=native,
                                    error=snap["error"])
                    snap["liquidations"] = tracker.summary(native)
                    await redis.set(KEY.format(symbol=native), json.dumps(snap), ex=TTL)
                log.info("derivatives_refreshed", symbols=sorted(natives))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        await redis.aclose()


async def main() -> None:
    import signal

    from nexus_common.logging import configure_logging
    from nexus_config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
