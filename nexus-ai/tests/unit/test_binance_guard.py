"""Shared Binance public-API back-off: parse refusals, pause every caller, fail closed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from nexus_common.binance_guard import (
    BinanceBackoff,
    blocked,
    guarded_get,
    note_exception,
    scope_for,
    until_from,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class R:
    def __init__(self, broken: bool = False) -> None:
        self.kv: dict[str, Any] = {}
        self.broken = broken

    async def get(self, k: str) -> Any:
        if self.broken:
            raise ConnectionError("down")
        return self.kv.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.kv[k] = v


def test_until_from_parses_binance_refusals() -> None:
    ms = int((NOW + timedelta(minutes=20)).timestamp() * 1000)
    text = f'{{"code":-1003,"msg":"Way too much request weight used; IP banned until {ms}."}}'
    assert until_from(418, text, None, NOW) == NOW + timedelta(minutes=20)
    assert until_from(429, "too many", "45", NOW) == NOW + timedelta(seconds=45)
    assert until_from(418, "", None, NOW) == NOW + timedelta(minutes=30)
    assert until_from(429, "", None, NOW) == NOW + timedelta(minutes=2)
    assert until_from(500, "server error", None, NOW) is None
    assert scope_for("https://fapi.binance.com/fapi/v1/klines") == "futures"
    assert scope_for("https://data-api.binance.vision/api/v3/klines") == "spot"


def test_one_refusal_pauses_every_later_call_for_that_api_only() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(418, headers={"retry-after": "600"}, text="I'm a teapot")

    async def go() -> None:
        r = R()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            resp = await guarded_get(c, r, "https://fapi.binance.com/fapi/v1/premiumIndex", {})
            assert resp.status_code == 418
            with pytest.raises(BinanceBackoff, match="futures API paused"):
                await guarded_get(c, r, "https://fapi.binance.com/fapi/v1/klines", {})
            assert await blocked(r, "futures") is not None
            assert await blocked(r, "spot") is None        # spot is a separate limit
    asyncio.run(go())
    assert len(calls) == 1                                   # the second call never left


def test_unreadable_state_means_do_not_call() -> None:
    assert asyncio.run(blocked(R(broken=True), "spot")) is not None


def test_note_exception_from_ccxt_style_message() -> None:
    r = R()
    ms = int((datetime.now(UTC) + timedelta(minutes=10)).timestamp() * 1000)
    exc = Exception(f'binance 418 I\'m a teapot {{"code":-1003,"msg":"IP banned until {ms}."}}')
    until = asyncio.run(note_exception(r, "spot", exc))
    assert until is not None and asyncio.run(blocked(r, "spot")) is not None
    assert asyncio.run(note_exception(R(), "spot", Exception("network error"))) is None


# ---------------------------------------------------------------- request-weight budget
def test_used_weight_reads_the_header_in_any_case() -> None:
    from nexus_common.binance_guard import used_weight

    assert used_weight({"X-MBX-USED-WEIGHT-1M": "1234"}) == 1234
    assert used_weight({"x-mbx-used-weight-1m": "7"}) == 7
    assert used_weight({"other": "1"}) is None and used_weight(None) is None
    assert used_weight({"x-mbx-used-weight-1m": "n/a"}) is None


async def test_weight_budget_pauses_every_caller_until_next_minute() -> None:
    from nexus_common.binance_guard import note_weight, weight_status

    r = R()
    now = datetime(2026, 11, 22, 12, 0, 30, tzinfo=UTC)
    assert await note_weight(r, "spot", 3000, now) is None           # 50%: warning only
    assert await note_weight(r, "spot", 4700, now) is None           # below 80%
    until = await note_weight(r, "spot", 4800, now)                  # 80% of 6000
    assert until == datetime(2026, 11, 22, 12, 1, 1, tzinfo=UTC)
    assert "spot" in "".join(r.kv)                                    # back-off recorded
    assert await note_weight(r, "futures", 1920, now) is not None    # 80% of 2400
    assert (await weight_status(r))["spot"] == {"used": 4800, "limit": 6000}
    assert await note_weight(r, "spot", None, now) is None


async def test_guarded_get_records_weight_from_headers() -> None:
    from nexus_common.binance_guard import weight_status

    r = R()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"X-MBX-USED-WEIGHT-1M": "4900"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await guarded_get(c, r, "https://data-api.binance.vision/api/v3/klines", {})
    assert (await weight_status(r))["spot"]["used"] == 4900
    assert await blocked(r, "spot") is not None                       # paused at 80%+


async def test_ccxt_adapter_reports_weight_to_its_hook() -> None:
    from nexus_common.binance_guard import attach_weight_hooks, weight_status
    from nexus_config import ExchangeCode
    from nexus_exchange import CAPABILITIES
    from nexus_exchange.ccxt_adapter import CcxtSpotAdapter

    from tests.exchange.fakes import FakeCcxtClient

    client = FakeCcxtClient()
    client.last_response_headers = {"x-mbx-used-weight-1m": "120"}
    adapter = CcxtSpotAdapter("binance", client, CAPABILITIES[ExchangeCode.BINANCE])
    r = R()
    assert attach_weight_hooks({"binance": adapter, "kraken": object()}, r) == 1
    await adapter.load_markets()
    assert (await weight_status(r))["spot"]["used"] == 120
