"""Instruments outside crypto that NEXUS knows about (multi-asset Stage 2), and base-currency
conversion.

Each instrument says which market it belongs to, what it is, where its data comes from,
its quote currency, its contract multiplier and its trading session. Contract details
for broker-traded instruments (tick, multiplier, hours) are read from the broker at
connection time, never typed in by hand. Only research instruments are listed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus_common.markets import Market

INSTRUMENT_TYPES = ("spot", "perpetual", "stock", "etf", "future", "fx_spot", "cfd", "index")


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    market: Market
    instrument_type: str
    venue: str                     # a venue code from nexus_common.markets
    quote_currency: str
    session: str                   # a kind from nexus_common.sessions
    contract_multiplier: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if self.instrument_type not in INSTRUMENT_TYPES:
            raise ValueError(f"unknown instrument type {self.instrument_type!r}")
        if self.contract_multiplier <= 0:
            raise ValueError("contract multiplier must be positive")


def _perp(symbol: str, name: str, market: Market, venue: str) -> Instrument:
    return Instrument(symbol, name, market, "perpetual", venue, "USDT", "24x7")


RESEARCH_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument("NDX", "NASDAQ-100 (FRED)", Market.STOCKS, "index", "fred_stocks", "USD",
               "XNYS"),
    *(_perp(s, n, Market.STOCKS, "binance_stock_perps") for s, n in (
        ("QQQUSDT", "QQQ (NASDAQ-100 ETF)"), ("SPYUSDT", "SPY (S&P 500 ETF)"),
        ("NVDAUSDT", "Nvidia"), ("AAPLUSDT", "Apple"), ("MSFTUSDT", "Microsoft"),
        ("TSLAUSDT", "Tesla"))),
    *(_perp(s, n, Market.COMMODITIES, "binance_commodity_perps") for s, n in (
        ("XAUUSDT", "Gold"), ("XAGUSDT", "Silver"), ("XPTUSDT", "Platinum"),
        ("XPDUSDT", "Palladium"), ("CLUSDT", "Crude oil"), ("NATGASUSDT", "Natural gas"),
        ("COPPERUSDT", "Copper"))),
    *(Instrument(p, p, Market.FOREX, "fx_spot", "fred_fx", p[3:], "fx_24x5") for p in (
        "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF")),
)
BY_SYMBOL = {i.symbol: i for i in RESEARCH_INSTRUMENTS}


def of_market(market: Market, venue: str | None = None) -> dict[str, str]:
    """symbol -> name for one market (optionally one venue), in registry order."""
    return {i.symbol: i.name for i in RESEARCH_INSTRUMENTS
            if i.market is market and (venue is None or i.venue == venue)}


class MissingRate(LookupError):
    """No conversion rate for a currency. NEXUS never guesses one."""


def to_base(amount: Decimal, currency: str, rates: dict[str, Decimal], *,
            base: str = "USD") -> Decimal:
    """Convert ``amount`` in ``currency`` to ``base``.

    ``rates[c]`` is the number of ``base`` units per 1 unit of ``c``; for example, with
    base USD, ``rates["JPY"]`` is about 0.0063. USDT is treated as USD only when
    ``rates`` says so explicitly. A missing rate raises ``MissingRate``.
    """
    if currency == base:
        return amount
    rate = rates.get(currency)
    if rate is None or rate <= 0:
        raise MissingRate(f"no {currency}->{base} rate")
    return amount * rate
