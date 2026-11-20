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
        async def f(api_key: str, secret: str, *, testnet: bool, cafile: str | None = None):  # type: ignore[no-untyped-def]
            calls.append((name, testnet))
            return {"ok": name == "futures"}
        return f

    res = await probe("k", "s", environment="sandbox", markets=["futures", "spot"],
                      probes={"futures": await ok("futures"), "spot": await ok("spot")})
    assert res["environment"] == "testnet" and res["ok"] is False
    assert calls == [("futures", True), ("spot", True)]
