"""Binance public market-data adapter (keyless).

Only GET requests to an allow-list of public market-data paths are possible.
There is no API key, no signing, and no account or order endpoint (ADR-004).
Klines: GET /api/v3/klines -> rows
  [openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..timeframes import TIMEFRAMES
from .ratelimit import TokenBucket
from .retry import MAX_ATTEMPTS, backoff_delay, parse_retry_after
from .types import (
    Candle,
    PricePoint,
    SourceBlockedError,
    SourceRequestError,
    SourceResponseError,
    SourceUnavailableError,
)

ALLOWED_PATHS = frozenset({"/api/v3/klines", "/api/v3/ticker/price", "/api/v3/ping", "/api/v3/time"})
MAX_KLINES_PER_REQUEST = 1000
# Request weights from the public docs; the bucket counts weight, not requests.
KLINES_WEIGHT = 2
SIMPLE_WEIGHT = 1


def _dec(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise SourceResponseError(f"{field}: expected decimal string")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise SourceResponseError(f"{field}: not a decimal") from None
    if not d.is_finite():
        raise SourceResponseError(f"{field}: not finite")
    return d


def _ms(value: Any, field: str) -> datetime:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceResponseError(f"{field}: expected epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def parse_kline_row(row: Any, now: datetime) -> Candle:
    if not isinstance(row, list) or len(row) < 9:
        raise SourceResponseError("kline row: unexpected shape")
    close_time = _ms(row[6], "closeTime")
    trades = row[8]
    if not isinstance(trades, int) or isinstance(trades, bool):
        raise SourceResponseError("trades: expected integer")
    return Candle(
        open_time=_ms(row[0], "openTime"),
        close_time=close_time,
        open=_dec(row[1], "open"),
        high=_dec(row[2], "high"),
        low=_dec(row[3], "low"),
        close=_dec(row[4], "close"),
        volume=_dec(row[5], "volume"),
        quote_volume=_dec(row[7], "quoteVolume"),
        trade_count=trades,
        # A candle is final only once its close time has passed.
        is_final=close_time < now,
    )


class BinancePublicClient:
    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 10.0,
        limiter: TokenBucket | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=timeout_s, headers={"Accept": "application/json"})
        self._limiter = limiter or TokenBucket(rate_per_s=300 / 60, capacity=20)
        self._sleep = sleep
        self._now = now
        self._blocked: str | None = None

    @property
    def blocked_reason(self) -> str | None:
        return self._blocked

    def _get(self, path: str, params: dict[str, str | int], weight: int) -> Any:
        if path not in ALLOWED_PATHS:
            raise SourceRequestError(f"path not allowed: {path}")
        if self._blocked:
            raise SourceBlockedError(self._blocked)
        last_error = "unknown"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._limiter.acquire(weight)
            try:
                resp = self._http.get(self._base_url + path, params=params)
            except httpx.TimeoutException:
                last_error = "timeout"
                self._sleep(backoff_delay(attempt))
                continue
            except httpx.TransportError as e:
                last_error = f"transport:{type(e).__name__}"
                self._sleep(backoff_delay(attempt))
                continue
            status = resp.status_code
            if status in (418, 451):
                # Banned (418) or unavailable from this location (451). Stop; never evade.
                self._blocked = f"HTTP {status} from source"
                raise SourceBlockedError(self._blocked)
            if status == 429:
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                self._limiter.penalize(retry_after or 1.0)
                last_error = "rate_limited"
                self._sleep(backoff_delay(attempt, retry_after))
                continue
            if status >= 500:
                last_error = f"http_{status}"
                self._sleep(backoff_delay(attempt))
                continue
            if status >= 400:
                raise SourceRequestError(f"HTTP {status}")
            try:
                return resp.json()
            except ValueError:
                raise SourceResponseError("invalid JSON") from None
        raise SourceUnavailableError(f"retries exhausted ({last_error})")

    def fetch_klines(
        self, symbol: str, tf: str, start: datetime | None = None, end: datetime | None = None, limit: int = MAX_KLINES_PER_REQUEST
    ) -> list[Candle]:
        if tf not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe {tf!r}")
        if not 1 <= limit <= MAX_KLINES_PER_REQUEST:
            raise ValueError("limit out of range")
        params: dict[str, str | int] = {"symbol": symbol, "interval": tf, "limit": limit}
        if start is not None:
            params["startTime"] = int(start.timestamp() * 1000)
        if end is not None:
            params["endTime"] = int(end.timestamp() * 1000)
        data = self._get("/api/v3/klines", params, KLINES_WEIGHT)
        if not isinstance(data, list):
            raise SourceResponseError("klines: expected a list")
        now = self._now()
        return [parse_kline_row(row, now) for row in data]

    def fetch_price(self, symbol: str) -> PricePoint:
        data = self._get("/api/v3/ticker/price", {"symbol": symbol}, SIMPLE_WEIGHT)
        if not isinstance(data, dict) or data.get("symbol") != symbol:
            raise SourceResponseError("ticker: unexpected payload")
        price = _dec(data.get("price"), "price")
        if price <= 0:
            raise SourceResponseError("ticker: non-positive price")
        # The endpoint carries no timestamp; record when we observed it.
        return PricePoint(price=price, source_time=self._now())

    def close(self) -> None:
        self._http.close()
