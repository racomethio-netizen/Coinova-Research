from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexus_config import ExchangeCode, Timeframe
from nexus_exchange import CAPABILITIES
from nexus_exchange.candles import aggregate_candles
from nexus_exchange.ccxt_adapter import CcxtSpotAdapter
from nexus_exchange.models import Candle

from tests.exchange.fakes import FakeCcxtClient

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def hourly(n: int, start: datetime = T0, skip: set[int] | None = None) -> list[Candle]:
    out = []
    for i in range(n):
        if skip and i in skip:
            continue
        o = D(100 + i)
        out.append(Candle(start + timedelta(hours=i), o, o + 2, o - 1, o + 1, D("1.5")))
    return out


def test_aggregate_complete_buckets() -> None:
    out = aggregate_candles(hourly(8), source_seconds=3600, target_seconds=14400)
    assert [c.open_time for c in out] == [T0, T0 + timedelta(hours=4)]
    first = out[0]
    assert (first.open, first.high, first.low, first.close) == (D(100), D(105), D(99), D(104))
    assert first.volume == D("6.0")


def test_incomplete_buckets_dropped_not_padded() -> None:
    out = aggregate_candles(hourly(8, skip={5}), source_seconds=3600, target_seconds=14400)
    assert [c.open_time for c in out] == [T0]
    # leading partial bucket (starts mid-bucket) dropped too
    out = aggregate_candles(hourly(7, start=T0 + timedelta(hours=1)), source_seconds=3600,
                            target_seconds=14400)
    assert [c.open_time for c in out] == [T0 + timedelta(hours=4)]


def test_invalid_factor() -> None:
    with pytest.raises(ValueError):
        aggregate_candles([], source_seconds=3600, target_seconds=5400)


async def test_coinbase_4h_is_built_from_1h() -> None:
    client = FakeCcxtClient()
    ms = int(T0.timestamp() * 1000)
    client.responses["fetch_ohlcv"] = [[ms + i * 3_600_000, 1, 2, 0.5, 1.5, 1] for i in range(10)]
    a = CcxtSpotAdapter("coinbase", client, CAPABILITIES[ExchangeCode.COINBASE])
    out = await a.fetch_closed_candles("BTC/USDT", Timeframe.H4, now=T0 + timedelta(hours=10))
    assert client.calls[-1][1][1] == "1h"
    assert [c.open_time for c in out] == [T0, T0 + timedelta(hours=4)]
