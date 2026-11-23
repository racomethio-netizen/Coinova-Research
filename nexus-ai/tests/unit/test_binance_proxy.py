"""Binance-only static-IP proxy: configuration, and real traffic through a local proxy."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from nexus_common import proxy

from tests.proxyserver import RecordingProxy


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for n in proxy.ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


def test_not_configured_changes_nothing() -> None:
    assert proxy.binance_proxy_url() is None and proxy.httpx_kwargs() == {}

    class C:
        pass
    c = proxy.apply_to_ccxt(C())
    assert not hasattr(c, "httpsProxy")


def test_quotaguard_name_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTAGUARDSTATIC_URL", "http://user:s3cret@eu-static.quotaguard.com:9293")
    assert proxy.binance_proxy_url() == "http://user:s3cret@eu-static.quotaguard.com:9293"
    assert proxy.describe() == "eu-static.quotaguard.com:9293"     # never the password
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", "http://own:pw@p.example:8080")
    assert proxy.describe() == "p.example:8080"                     # NEXUS name wins


def test_malformed_url_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", "socks5://h:1")
    with pytest.raises(ValueError, match="http"):
        proxy.binance_proxy_url()


def test_every_binance_client_factory_uses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", "http://u:p@127.0.0.1:9")
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport
    from nexus_exchange.ccxt_adapter import create_ccxt_client

    spot = create_ccxt_client("binance")
    assert spot.httpsProxy == "http://u:p@127.0.0.1:9"
    fut = CcxtFuturesTransport("k" * 32, "s" * 48)
    assert fut._client.httpsProxy == "http://u:p@127.0.0.1:9"
    assert getattr(create_ccxt_client("kraken"), "httpsProxy", None) is None  # Binance only


# ---------------------------------------------------------------- real traffic
async def _site() -> tuple[web.AppRunner, int]:
    async def time_(_: web.Request) -> web.Response:
        return web.json_response({"serverTime": 1_767_225_600_000})

    async def ws(request: web.Request) -> web.WebSocketResponse:
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        await sock.send_str(json.dumps({"stream": "btcusdt@depth20", "data": {}}))
        await sock.close()
        return sock

    app = web.Application()
    app.router.add_get("/api/v3/time", time_)
    app.router.add_get("/stream", ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]


async def test_traffic_goes_through_the_proxy_with_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp
    import httpx
    import websockets
    from nexus_exchange.ccxt_adapter import create_ccxt_client

    px = RecordingProxy("user", "pw")
    await px.start()
    runner, port = await _site()
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", f"http://user:pw@127.0.0.1:{px.port}")
    base = f"http://127.0.0.1:{port}"
    try:
        # 1) httpx (derivatives, carry paper, forward test, dashboard data)
        async with httpx.AsyncClient(timeout=5, trust_env=False, **proxy.httpx_kwargs()) as c:
            assert (await c.get(f"{base}/api/v3/time")).json()["serverTime"]
        # 2) ccxt Binance client (market data, trader, account pages)
        client: Any = create_ccxt_client("binance")
        client.urls["api"]["public"] = f"{base}/api/v3"
        try:
            assert await client.fetch_time() == 1_767_225_600_000
        finally:
            await client.close()
        # 3) aiohttp WebSocket (the spot market stream)
        async with aiohttp.ClientSession() as s, \
                s.ws_connect(f"ws://127.0.0.1:{port}/stream",
                             proxy=proxy.binance_proxy_url()) as w:
            assert "depth20" in (await w.receive()).data
        # 4) websockets (the liquidation stream)
        async with websockets.connect(f"ws://127.0.0.1:{port}/stream",
                                      proxy=proxy.binance_proxy_url()) as w2:
            assert "depth20" in str(await w2.recv())
    finally:
        await runner.cleanup()
        await px.stop()
    assert len(px.seen) >= 4 and px.denied == 0
    assert all(f"127.0.0.1:{port}" in s for s in px.seen)


async def test_wrong_password_is_refused_not_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    px = RecordingProxy("user", "pw")
    await px.start()
    runner, port = await _site()
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", f"http://user:WRONG@127.0.0.1:{px.port}")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, **proxy.httpx_kwargs()) as c:
            r = await c.get(f"http://127.0.0.1:{port}/api/v3/time")
        assert r.status_code == 407        # refused by the proxy; never sent directly
    finally:
        await runner.cleanup()
        await px.stop()
    assert px.denied == 1
    await asyncio.sleep(0)


async def test_market_stream_connects_through_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEXUS's own spot-stream connector (not just the library) uses the proxy."""
    from nexus_market.binance_ws import BinanceMarketStream

    from tests.memredis import MemRedis

    px = RecordingProxy("user", "pw")
    await px.start()
    runner, port = await _site()
    monkeypatch.setenv("NEXUS_BINANCE_PROXY_URL", f"http://user:pw@127.0.0.1:{px.port}")

    async def noop(*_: Any) -> None:
        return None

    stream = BinanceMarketStream(["BTC/USDT"], ["1m"], MemRedis(), on_book=noop, on_kline=noop,
                                 ws_url=f"ws://127.0.0.1:{port}")
    try:
        conn = await stream._aiohttp_connect(f"ws://127.0.0.1:{port}/stream")
        try:
            assert "depth20" in (await conn.receive() or "")
        finally:
            await conn.close()
    finally:
        await runner.cleanup()
        await px.stop()
    assert any(f"127.0.0.1:{port}" in s for s in px.seen) and px.denied == 0
