"""Stage 2: trading sessions (weekends, holidays, daylight saving), the instrument
registry, and base-currency conversion that never guesses a rate."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexus_common.instruments import (
    BY_SYMBOL,
    RESEARCH_INSTRUMENTS,
    Instrument,
    MissingRate,
    of_market,
    to_base,
)
from nexus_common.markets import MARKETS, Market
from nexus_common.sessions import is_open, next_open


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def test_crypto_never_closes_and_naive_times_are_refused():
    assert is_open("24x7", utc(2026, 12, 25, 3, 0))
    with pytest.raises(ValueError):
        is_open("24x7", datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError):
        is_open("lunar", utc(2026, 1, 1))


def test_fx_week_follows_new_york_17_00_with_daylight_saving():
    # Winter (EST, UTC-5): opens Sunday 22:00 UTC, closes Friday 22:00 UTC.
    assert not is_open("fx_24x5", utc(2026, 11, 29, 21, 59))      # Sunday
    assert is_open("fx_24x5", utc(2026, 11, 29, 22, 0))
    assert is_open("fx_24x5", utc(2026, 12, 4, 21, 59))            # Friday
    assert not is_open("fx_24x5", utc(2026, 12, 4, 22, 0))
    assert not is_open("fx_24x5", utc(2026, 12, 5, 12, 0))         # Saturday
    # Summer (EDT, UTC-4): the same New York times are one hour earlier in UTC.
    assert is_open("fx_24x5", utc(2026, 7, 5, 21, 0))              # Sunday 17:00 NY
    assert not is_open("fx_24x5", utc(2026, 7, 10, 21, 0))         # Friday 17:00 NY
    assert next_open("fx_24x5", utc(2026, 12, 5, 12, 0)) == utc(2026, 12, 6, 22, 0)


def test_nyse_holidays_and_early_close():
    assert not is_open("XNYS", utc(2026, 11, 26, 16, 0))           # Thanksgiving
    assert is_open("XNYS", utc(2026, 11, 27, 15, 0))               # day after, morning
    assert not is_open("XNYS", utc(2026, 11, 27, 19, 0))           # early close 13:00 ET
    assert not is_open("XNYS", utc(2026, 11, 28, 15, 0))           # Saturday
    nxt = next_open("XNYS", utc(2026, 11, 26, 16, 0))
    assert nxt is not None and nxt == utc(2026, 11, 27, 14, 30)


def test_registry_is_consistent_with_the_market_venues():
    venues = {v.code for spec in MARKETS.values() for v in spec.venues}
    for i in RESEARCH_INSTRUMENTS:
        assert i.venue in venues, i
        assert i.session in ("24x7", "fx_24x5", "XNYS", "CMES")
    assert "SPXUSDT" not in BY_SYMBOL                              # a memecoin, not the S&P
    assert BY_SYMBOL["USDJPY"].quote_currency == "JPY"
    assert list(of_market(Market.COMMODITIES))[:2] == ["XAUUSDT", "XAGUSDT"]
    with pytest.raises(ValueError):
        Instrument("X", "x", Market.STOCKS, "bond", "ibkr", "USD", "XNYS")
    with pytest.raises(ValueError):
        Instrument("X", "x", Market.STOCKS, "stock", "ibkr", "USD", "XNYS", Decimal(0))


def test_base_currency_conversion_never_guesses():
    rates = {"EUR": Decimal("1.0981"), "JPY": Decimal("0.0063")}
    assert to_base(Decimal(100), "USD", rates) == Decimal(100)
    assert to_base(Decimal(100), "EUR", rates) == Decimal("109.81")
    assert to_base(Decimal(10_000), "JPY", rates) == Decimal("63.0000")
    with pytest.raises(MissingRate):
        to_base(Decimal(1), "USDT", rates)                         # not assumed equal to USD
    with pytest.raises(MissingRate):
        to_base(Decimal(1), "CHF", {"CHF": Decimal(0)})
