"""Live Binance account views (read-only) for the Spot and Futures dashboards.

Data comes straight from Binance with the operator's stored key. Nothing is estimated
or filled in: without a key the view says "not connected", and on failure it shows
Binance's error. Results are cached for a few seconds to respect rate limits.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt_async
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models
from nexus_exchange.binance_futures import BinanceFuturesAdapter
from nexus_exchange.binance_futures_transport import CcxtFuturesTransport
from nexus_exchange.factory import load_binance_spot_markets
from nexus_security import CredentialCipher, KeyRing
from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus_api import binance_ban
from nexus_api.connection import explain

CACHE_SECONDS = 60


def select_credential(s: Session, user_id: int, market: str) -> models.ExchangeCredential | None:
    """The user's active Binance key for ``market``; the most recently verified wins."""
    rows = s.scalars(
        select(models.ExchangeCredential)
        .join(models.Exchange, models.Exchange.id == models.ExchangeCredential.exchange_id)
        .where(models.ExchangeCredential.user_id == user_id,
               models.ExchangeCredential.revoked_at.is_(None),
               models.Exchange.code == "binance")
    ).all()
    usable = [c for c in rows if market in ((c.permissions or {}).get("markets") or [])]
    usable.sort(key=lambda c: (c.last_verified_at is not None, c.last_verified_at or c.created_at,
                               c.id), reverse=True)
    return usable[0] if usable else None


def decrypt(cred: models.ExchangeCredential, settings: Settings) -> tuple[str, str]:
    if settings.credential_encryption_keys is None:
        raise RuntimeError("encryption not configured")
    cipher = CredentialCipher(KeyRing.parse(settings.credential_encryption_keys.get_secret_value()))
    ctx = {"user_id": str(cred.user_id), "exchange": "binance", "credential_id": str(cred.id)}
    return (cipher.decrypt(cred.api_key_encrypted, context={**ctx, "field": "api_key"}),
            cipher.decrypt(cred.api_secret_encrypted, context={**ctx, "field": "api_secret"}))


def _sum(rows: list[dict[str, Any]]) -> str:
    return str(sum((Decimal(r["income"]) for r in rows), Decimal(0)))


async def futures_snapshot(api_key: str, secret: str, *, environment: str,
                           cafile: str | None = None) -> dict[str, Any]:
    transport = CcxtFuturesTransport(api_key, secret, cafile=cafile, environment=environment)
    try:
        ad = BinanceFuturesAdapter(transport)
        acct = await ad.account()
        positions = await ad.positions()
        orders = await ad.open_orders()
        since = int((utcnow() - timedelta(days=7)).timestamp() * 1000)
        realized = await ad.income("REALIZED_PNL", since)
        funding = await ad.income("FUNDING_FEE", since)
        fees = await ad.income("COMMISSION", since)
        return {
            "account": {k: str(v) for k, v in acct.items()},
            "positions": [{
                "symbol": p.symbol, "side": p.side, "quantity": str(abs(p.quantity)),
                "entry_price": str(p.entry_price), "mark_price": str(p.mark_price),
                "liquidation_price": str(p.liquidation_price) if p.liquidation_price else None,
                "liquidation_distance": (str(p.liquidation_distance)
                                         if p.liquidation_distance is not None else None),
                "leverage": p.leverage, "margin_type": p.margin_type,
                "unrealized_pnl": str(p.unrealized_pnl)} for p in positions],
            "open_orders": orders,
            "last_7_days": {"realized_pnl": _sum(realized), "funding": _sum(funding),
                            "commission": _sum(fees)},
        }
    finally:
        await transport.close()


async def spot_snapshot(api_key: str, secret: str, *, environment: str, pairs: list[str],
                        cafile: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                              "timeout": 10_000, "aiohttp_trust_env": True,
                              "options": {"adjustForTimeDifference": True,
                                          "warnOnFetchOpenOrdersWithoutSymbol": False}}
    if cafile:
        config["cafile"] = cafile
    from nexus_common.binance_guard import gate_client

    client = gate_client(ccxt_async.binance(config), environment=environment)
    if environment == "demo":
        client.enable_demo_trading(True)
    elif environment == "testnet":
        client.set_sandbox_mode(True)
    try:
        await load_binance_spot_markets(client, pairs)
        bal = await client.fetch_balance()
        balances = [{"asset": a, "free": str(bal["free"].get(a) or 0),
                     "locked": str(bal["used"].get(a) or 0), "total": str(t)}
                    for a, t in (bal.get("total") or {}).items() if t]
        # Per symbol (weight 6 each) instead of all symbols at once (weight 80).
        orders = [o for pair in pairs for o in await client.fetch_open_orders(pair)]
        trades: list[dict[str, Any]] = []
        for pair in pairs:
            for t in await client.fetch_my_trades(pair, limit=20):
                trades.append({"symbol": t["symbol"], "side": t["side"], "price": str(t["price"]),
                               "amount": str(t["amount"]), "fee": t.get("fee"),
                               "time": t["datetime"]})
        trades.sort(key=lambda t: t["time"] or "", reverse=True)
        return {
            "balances": sorted(balances, key=lambda b: b["asset"]),
            "open_orders": [{"symbol": o["symbol"], "side": o["side"], "type": o["type"],
                             "price": str(o["price"]), "amount": str(o["amount"]),
                             "filled": str(o["filled"]), "status": o["status"],
                             "client_order_id": o.get("clientOrderId"), "time": o["datetime"]}
                            for o in orders],
            "recent_trades": trades[:50],
        }
    finally:
        await client.close()


async def account_view(market: str, s: Session, user_id: int, settings: Settings,
                       redis: Any) -> dict[str, Any]:
    cred = select_credential(s, user_id, market)
    if cred is None:
        return {"connected": False, "market": market,
                "message": f"No Binance key with {market} access is connected."}
    from nexus_api.connection import ENV_NAMES

    environment = ENV_NAMES.get(cred.environment, cred.environment)
    base = {"connected": True, "market": market, "environment": environment,
            "label": cred.label, "fingerprint": cred.key_fingerprint}
    cache_key = f"nexus:account:{market}:{cred.id}"
    cached = await redis.get(cache_key)
    if cached:
        return {**base, **json.loads(cached), "cached": True}
    banned = await binance_ban.active_ban(redis, environment)
    if banned is not None:
        return {**base, "ok": False, "error": binance_ban.ban_message(banned),
                "banned_until": banned.isoformat(), "fetched_at": utcnow().isoformat(),
                "cached": False}
    api_key, secret = decrypt(cred, settings)
    try:
        if market == "futures":
            data = await futures_snapshot(api_key, secret, environment=environment,
                                          cafile=settings.extra_ca_file)
        else:
            data = await spot_snapshot(api_key, secret, environment=environment,
                                       pairs=list(settings.trading_pairs),
                                       cafile=settings.extra_ca_file)
        payload: dict[str, Any] = {"ok": True, "data": data, "fetched_at": utcnow().isoformat()}
    except Exception as exc:
        error = explain(exc)
        until = await binance_ban.record_ban(redis, environment, f"{exc} {exc.__cause__ or ''}")
        if until is not None:
            error = binance_ban.ban_message(until)
        payload = {"ok": False, "error": error, "fetched_at": utcnow().isoformat()}
    await redis.set(cache_key, json.dumps(payload), ex=CACHE_SECONDS)
    return {**base, **payload, "cached": False}
