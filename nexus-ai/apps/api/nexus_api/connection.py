"""Binance connectivity probe for a stored credential.

Makes real signed, read-only calls (no orders). Returns a JSON-safe summary that never
contains the key or secret. Binance error texts are reduced to their code and message.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt_async
from nexus_common.timeutil import utcnow
from nexus_exchange.binance_futures import BinanceFuturesAdapter
from nexus_exchange.binance_futures_transport import CcxtFuturesTransport
from nexus_exchange.factory import load_binance_spot_markets

_BINANCE_MSG = re.compile(r'"code"\s*:\s*(-?\d+)\s*,\s*"msg"\s*:\s*"([^"]{0,200})"')


def explain(exc: BaseException) -> str:
    """Plain, secret-free explanation of a failed Binance call."""
    # Adapters wrap library errors; the original ccxt error (with Binance's body) is the cause.
    cause = exc.__cause__
    if isinstance(cause, ccxt_async.BaseError):
        exc = cause
    text = str(exc)
    m = _BINANCE_MSG.search(text)
    detail = f" (Binance {m.group(1)}: {m.group(2)})" if m else ""
    if "451" in text or "restricted location" in text.lower():
        return "Binance refuses requests from this server's location (HTTP 451)" + detail
    if isinstance(exc, ccxt_async.AuthenticationError) or (m and m.group(1) in {"-2014", "-2015"}):
        return "authentication failed: check the key, the secret and the key's IP whitelist" + detail
    if isinstance(exc, ccxt_async.PermissionDenied):
        return "the key lacks the permission for this market" + detail
    if isinstance(exc, ccxt_async.InvalidNonce) or (m and m.group(1) == "-1021"):
        return "timestamp rejected: server clock is out of sync" + detail
    if isinstance(exc, ccxt_async.NetworkError):
        return "network error reaching Binance" + detail
    return f"{type(exc).__name__}{detail}"


async def probe_futures(api_key: str, secret: str, *, environment: str,
                        cafile: str | None = None) -> dict[str, Any]:
    transport = CcxtFuturesTransport(api_key, secret, cafile=cafile, environment=environment)
    try:
        adapter = BinanceFuturesAdapter(transport)
        await adapter.ping()
        acct = await adapter.account()
        hedge = await adapter.hedge_mode_enabled()
        return {"ok": True, "wallet_balance_usdt": str(acct["wallet_balance"]),
                "available_balance_usdt": str(acct["available_balance"]),
                "unrealized_pnl_usdt": str(acct["unrealized_pnl"]),
                "position_mode": "hedge" if hedge else "one-way",
                "note": "NEXUS requires one-way position mode" if hedge else None}
    except Exception as exc:
        return {"ok": False, "error": explain(exc)}
    finally:
        await transport.close()


async def probe_spot(api_key: str, secret: str, *, environment: str,
                     cafile: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                              "timeout": 10_000, "options": {"adjustForTimeDifference": True},
                              "aiohttp_trust_env": True}
    if cafile:
        config["cafile"] = cafile
    client = ccxt_async.binance(config)
    if environment == "demo":
        client.enable_demo_trading(True)
    elif environment == "testnet":
        client.set_sandbox_mode(True)
    try:
        await load_binance_spot_markets(client, ["BTC/USDT"])
        bal = await client.fetch_balance()
        totals = {k: v for k, v in (bal.get("total") or {}).items() if v}
        result: dict[str, Any] = {"ok": True, "assets_with_balance": len(totals),
                                  "usdt_total": str(Decimal(str(totals.get("USDT", 0))))}
        if environment == "production":
            # Production only: refuse keys that can withdraw.
            restrictions = await client.sapi_get_account_apirestrictions()
            if restrictions.get("enableWithdrawals"):
                return {"ok": False, "error": "this key allows WITHDRAWALS: create a new key "
                        "with withdrawals disabled"}
            result["spot_trading_enabled"] = bool(restrictions.get("enableSpotAndMarginTrading"))
            result["futures_enabled"] = bool(restrictions.get("enableFutures"))
        return result
    except Exception as exc:
        return {"ok": False, "error": explain(exc)}
    finally:
        await client.close()


# Stored value -> name used by transports and shown to the operator.
ENV_NAMES = {"production": "production", "sandbox": "testnet", "demo": "demo"}
ENV_STORED = {v: k for k, v in ENV_NAMES.items()}

Prober = Callable[..., Awaitable[dict[str, Any]]]
PROBES: dict[str, Prober] = {"futures": probe_futures, "spot": probe_spot}


async def probe(api_key: str, secret: str, *, environment: str, markets: list[str],
                cafile: str | None = None,
                probes: dict[str, Prober] | None = None) -> dict[str, Any]:
    table = probes or PROBES
    name = ENV_NAMES.get(environment, environment)
    out: dict[str, Any] = {"checked_at": utcnow().isoformat(), "environment": name}
    for market in markets:
        out[market] = await table[market](api_key, secret, environment=name, cafile=cafile)
    out["ok"] = all(out[m]["ok"] for m in markets)
    return out
