"""Source-neutral market-data types. Prices and volumes are Decimal (ADR-009)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None
    trade_count: int | None
    is_final: bool

    def ohlcv(self) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        return (self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True, slots=True)
class PricePoint:
    price: Decimal
    source_time: datetime


class MarketDataError(Exception):
    """Base class for source errors. Messages never include credentials (there are none)."""


class SourceRequestError(MarketDataError):
    """Non-retryable request error (4xx other than rate limiting)."""


class SourceResponseError(MarketDataError):
    """The source returned a malformed or inconsistent payload."""


class SourceUnavailableError(MarketDataError):
    """Retries exhausted on timeouts, 5xx or rate limiting."""


class SourceBlockedError(MarketDataError):
    """HTTP 418/451: banned or unavailable from this location. Stop and alert; never evade."""


class SourceNotPermittedError(MarketDataError):
    """Gate G-4: the source's terms review is not recorded (production) or it is disabled."""
