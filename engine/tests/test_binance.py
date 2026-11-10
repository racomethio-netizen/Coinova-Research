"""Adapter tests against an in-process mock transport with synthetic payloads (no network)."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from nexus_engine.marketdata.binance import BinancePublicClient, parse_kline_row
from nexus_engine.marketdata.ratelimit import TokenBucket
from nexus_engine.marketdata.types import SourceBlockedError, SourceRequestError, SourceResponseError, SourceUnavailableError

NOW = datetime(2026, 1, 1, 5, 30, tzinfo=UTC)
H_MS = 3_600_000
T0_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def row(i: int) -> list[object]:
    o = T0_MS + i * H_MS
    return [o, "100.10", "102.00", "99.50", "101.00", "12.5", o + H_MS - 1, "1262.5", 42, "6", "600", "0"]


def client(handler: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request] | None = None) -> BinancePublicClient:
    def wrapped(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return handler(req)

    return BinancePublicClient(
        "https://example.test",
        http=httpx.Client(transport=httpx.MockTransport(wrapped)),
        limiter=TokenBucket(1000, 1000),
        sleep=lambda _: None,
        now=lambda: NOW,
    )


def test_parses_klines_as_decimals_and_marks_only_closed_candles_final() -> None:
    seen: list[httpx.Request] = []
    c = client(lambda r: httpx.Response(200, json=[row(i) for i in range(6)]), seen)
    candles = c.fetch_klines("BTCUSDT", "1h", start=datetime(2026, 1, 1, tzinfo=UTC))
    assert candles[0].open == Decimal("100.10") and candles[0].trade_count == 42
    assert [k.is_final for k in candles] == [True] * 5 + [False]  # the 05:00 candle is still open at 05:30
    q = seen[0].url.params
    assert seen[0].method == "GET" and seen[0].url.path == "/api/v3/klines"
    assert (q["symbol"], q["interval"], q["limit"], q["startTime"]) == ("BTCUSDT", "1h", "1000", str(T0_MS))


def test_sends_no_credentials() -> None:
    seen: list[httpx.Request] = []
    client(lambda r: httpx.Response(200, json=[]), seen).fetch_klines("BTCUSDT", "1h")
    headers = {k.lower() for k in seen[0].headers}
    assert "x-mbx-apikey" not in headers and "authorization" not in headers
    assert "signature" not in seen[0].url.params


def test_retries_rate_limits_and_server_errors_then_succeeds() -> None:
    responses = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(503), httpx.Response(200, json=[row(0)])]
    c = client(lambda r: responses.pop(0))
    assert len(c.fetch_klines("BTCUSDT", "1h")) == 1


def test_gives_up_after_bounded_retries() -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(SourceUnavailableError):
        client(lambda r: httpx.Response(500), calls).fetch_klines("BTCUSDT", "1h")
    assert len(calls) == 5


@pytest.mark.parametrize("status", [418, 451])
def test_ban_or_location_block_stops_all_traffic(status: int) -> None:
    calls: list[httpx.Request] = []
    c = client(lambda r: httpx.Response(status), calls)
    with pytest.raises(SourceBlockedError):
        c.fetch_klines("BTCUSDT", "1h")
    with pytest.raises(SourceBlockedError):
        c.fetch_price("BTCUSDT")
    assert len(calls) == 1 and c.blocked_reason == f"HTTP {status} from source"


def test_client_errors_are_not_retried() -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(SourceRequestError):
        client(lambda r: httpx.Response(400, json={"code": -1121}), calls).fetch_klines("NOPE", "1h")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [{"not": "a list"}, [[1, 2]], [[T0_MS, 100, "1", "1", "1", "1", T0_MS + H_MS - 1, "1", 1]], [[T0_MS, "x", "1", "1", "1", "1", T0_MS + H_MS - 1, "1", 1]]],
)
def test_malformed_payloads_are_errors(payload: object) -> None:
    with pytest.raises(SourceResponseError):
        client(lambda r: httpx.Response(200, json=payload)).fetch_klines("BTCUSDT", "1h")


def test_ticker_price() -> None:
    c = client(lambda r: httpx.Response(200, json={"symbol": "ETHUSDT", "price": "3000.50"}))
    p = c.fetch_price("ETHUSDT")
    assert p.price == Decimal("3000.50") and p.source_time == NOW
    with pytest.raises(SourceResponseError):
        client(lambda r: httpx.Response(200, json={"symbol": "BTCUSDT", "price": "1"})).fetch_price("ETHUSDT")


def test_only_allow_listed_paths() -> None:
    c = client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(SourceRequestError):
        c._get("/api/v3/order", {}, 1)


def test_bool_is_not_a_timestamp() -> None:
    with pytest.raises(SourceResponseError):
        parse_kline_row([True, "1", "1", "1", "1", "1", 5, "1", 1], NOW)
