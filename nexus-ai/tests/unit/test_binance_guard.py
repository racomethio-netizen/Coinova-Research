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
