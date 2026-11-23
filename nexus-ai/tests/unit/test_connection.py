from __future__ import annotations

import ccxt.async_support as ccxt
from nexus_api.connection import explain, probe


def test_explain_location_block() -> None:
    msg = explain(ccxt.ExchangeNotAvailable(
        'binance 451 {"code":0,"msg":"Service unavailable from a restricted location"}'))
    assert "HTTP 451" in msg


def test_explain_auth_failure_keeps_binance_code_only() -> None:
    msg = explain(ccxt.AuthenticationError(
        'binanceusdm {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'))
    assert msg.startswith("authentication failed") and "-2015" in msg


def test_explain_timestamp() -> None:
    assert "clock" in explain(ccxt.InvalidNonce('{"code":-1021,"msg":"Timestamp ahead"}'))


async def test_probe_combines_markets_and_marks_testnet() -> None:
    calls: list[tuple[str, bool]] = []

    async def ok(name: str):  # type: ignore[no-untyped-def]
        async def f(api_key: str, secret: str, *, environment: str, cafile: str | None = None):  # type: ignore[no-untyped-def]
            calls.append((name, environment))
            return {"ok": name == "futures"}
        return f

    res = await probe("k", "s", environment="sandbox", markets=["futures", "spot"],
                      probes={"futures": await ok("futures"), "spot": await ok("spot")})
    assert res["environment"] == "testnet" and res["ok"] is False
    assert calls == [("futures", "testnet"), ("spot", "testnet")]


async def test_demo_keys_are_refused() -> None:
    """Binance Demo Trading is not supported (owner decision 2026-11-23)."""
    import pytest
    from nexus_api.connection import probe_futures, probe_spot
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    for fn in (probe_futures, probe_spot):
        res = await fn("k" * 32, "s" * 48, environment="demo")
        assert res == {"ok": False, "error": "demo keys are not supported"}
    with pytest.raises(ValueError, match="unknown environment"):
        CcxtFuturesTransport("k" * 32, "s" * 48, environment="demo")


def test_transport_testnet_hosts() -> None:
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    t2 = CcxtFuturesTransport("k" * 32, "s" * 48, environment="testnet")
    assert t2._client.urls["api"]["fapiPrivateV2"].startswith("https://testnet.binancefuture.com")
