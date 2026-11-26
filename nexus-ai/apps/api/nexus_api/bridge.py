"""Read-only broker bridge endpoint (Exness MetaTrader 5 first).

The bridge (``apps/mt5_bridge``) runs on the owner's Windows machine next to a logged-in
MT5 terminal. It connects OUT to NEXUS; NEXUS never connects to the owner's computer, and
the MT5 password never leaves the terminal.

Each snapshot carries contract details, closed daily bars, quotes and an account summary.
NEXUS stores:
* contract details in ``trading_pairs`` (exchange ``exness``, with asset class, type and
  contract size);
* closed daily bars in ``market_candles`` (source ``mt5``), converted from the broker's
  server time to UTC;
* a summary in Redis, with the account number masked.

There is no order endpoint: nothing here can trade.

Authentication: ``Authorization: Bearer <token>``. The token is created by the owner
(``/api/v1/bridge-tokens``), shown once, stored only as a SHA-256 hash, and revocable. The
endpoint is rate-limited per IP and has a body size limit.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_db import models, repo
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexus_api.deps import client_ip, db, redis_dep

log = get_logger("nexus.api.bridge")

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])

EXCHANGE = "exness"
STATUS_KEY = "nexus:broker:exness:status"
MAX_BODY = 4_000_000
RATE_LIMIT, RATE_WINDOW = 120, 600          # requests per IP per 10 minutes
HISTORY_WANTED = 300                         # daily bars before asking for no more history
FX = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "SGD",
      "HKD", "ZAR", "MXN", "TRY", "PLN", "CZK", "HUF", "CNH", "THB", "ILS"}
METALS_ENERGY = ("XAU", "XAG", "XPT", "XPD", "USOIL", "UKOIL", "XNG", "XBR", "XTI")
SYMBOL_RE = r"^[A-Za-z0-9._#-]{1,32}$"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def classify(base_name: str) -> tuple[str, str]:
    """(asset_class, instrument_type) for an MT5 symbol, by its base name."""
    b = base_name.upper()
    if len(b) == 6 and b[:3] in FX and b[3:] in FX:
        return "forex", "fx_spot"
    if b.startswith(METALS_ENERGY):
        return "commodities", "cfd"
    return "stocks", "cfd"                   # indices and single stocks


class SymbolIn(BaseModel):
    name: str = Field(pattern=SYMBOL_RE)               # broker symbol, e.g. EURUSDm
    base_name: str = Field(pattern=SYMBOL_RE)          # what the owner asked for, e.g. EURUSD
    description: str = Field(default="", max_length=128)
    digits: int = Field(ge=0, le=10)
    point: Decimal = Field(gt=0)
    contract_size: Decimal = Field(gt=0)
    volume_min: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    volume_max: Decimal = Field(gt=0)
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    spread_points: int | None = Field(default=None, ge=0)
    currency_base: str = Field(default="", max_length=16)
    currency_profit: str = Field(default="", max_length=16)
    bid: Decimal | None = None
    ask: Decimal | None = None
    quote_time: int | None = None                      # server time, seconds


class PositionIn(BaseModel):
    symbol: str = Field(pattern=SYMBOL_RE)
    side: str = Field(pattern=r"^(buy|sell)$")
    volume: Decimal = Field(gt=0)
    price_open: Decimal
    profit: Decimal
    swap: Decimal = Decimal(0)


class AccountIn(BaseModel):
    login: int
    trade_mode: str = Field(pattern=r"^(demo|contest|real)$")
    currency: str = Field(max_length=16)
    balance: Decimal
    equity: Decimal
    margin: Decimal
    margin_free: Decimal
    leverage: int = Field(ge=0)
    server: str = Field(max_length=64)
    company: str = Field(max_length=128)


class SnapshotIn(BaseModel):
    bridge_version: str = Field(max_length=16)
    read_only: bool
    server_utc_offset_hours: int = Field(ge=-14, le=14)
    account: AccountIn
    symbols: list[SymbolIn] = Field(max_length=200)
    bars: dict[str, list[list[float]]] = Field(default_factory=dict)   # name -> [t,o,h,l,c,v]
    positions: list[PositionIn] = Field(default_factory=list, max_length=500)
    unresolved: list[str] = Field(default_factory=list, max_length=200)


async def bridge_auth(request: Request, s: Session = Depends(db),
                      redis: Any = Depends(redis_dep)) -> models.BrokerBridge:
    ip = client_ip(request)
    key = f"nexus:rl:bridge:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, RATE_WINDOW)
    if n > RATE_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or len(header) > 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bridge token required")
    bridge = s.scalar(select(models.BrokerBridge).where(
        models.BrokerBridge.token_hash == token_hash(header[7:].strip()),
        models.BrokerBridge.revoked_at.is_(None)))
    if bridge is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked bridge token")
    return bridge


def _exchange_id(s: Session) -> int:
    ex = s.scalar(select(models.Exchange).where(models.Exchange.code == EXCHANGE))
    if ex is None:
        # The seeded exchanges have explicit ids, so the next id is chosen explicitly too.
        next_id = int(s.scalar(select(func.coalesce(func.max(models.Exchange.id), 0))) or 0) + 1
        ex = models.Exchange(id=next_id, code=EXCHANGE, name="Exness (MetaTrader 5)",
                             is_enabled=False,
                             capabilities={"read_only_bridge": True, "orders": False})
        s.add(ex)
        s.flush()
    return int(ex.id)


def _store_symbol(s: Session, ex_id: int, sym: SymbolIn, now: datetime) -> int:
    asset_class, itype = classify(sym.base_name)
    base = sym.currency_base or sym.base_name[:3]
    quote = sym.currency_profit or sym.base_name[3:6] or "USD"
    pid = repo.upsert_trading_pair(
        s, exchange_id=ex_id, symbol=sym.base_name.upper(), exchange_symbol=sym.name,
        base=base[:16], quote=quote[:16], price_tick=sym.point, amount_step=sym.volume_step,
        min_amount=sym.volume_min, min_notional=None, maker_fee=None, taker_fee=None,
        synced_at=now)
    pair = s.get(models.TradingPair, pid)
    if pair is not None:
        pair.asset_class, pair.instrument_type = asset_class, itype
        pair.contract_multiplier = sym.contract_size
    return pid


def _closed_bars(rows: list[list[float]], offset_h: int, now: datetime) -> list[repo.CandleRow]:
    """Daily bars in broker server time -> UTC; the forming bar and bad rows are dropped."""
    out = []
    for r in rows:
        if len(r) < 6 or not all(x == x for x in r[:5]) or min(r[1:5]) <= 0:
            continue
        open_utc = datetime.fromtimestamp(int(r[0]), tz=UTC) - timedelta(hours=offset_h)
        if open_utc + timedelta(days=1) > now:
            continue
        o, h, lo, c = (Decimal(str(x)) for x in r[1:5])
        if h < max(o, c) or lo > min(o, c):
            continue
        out.append(repo.CandleRow(open_utc, o, h, lo, c, Decimal(str(max(r[5], 0)))))
    return out


@router.post("/mt5/snapshot")
async def mt5_snapshot(request: Request, s: Session = Depends(db),
                       redis: Any = Depends(redis_dep),
                       bridge: models.BrokerBridge = Depends(bridge_auth)) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "snapshot too large")
    try:
        snap = SnapshotIn.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid snapshot: {str(exc)[:300]}") from None
    if not snap.read_only:
        raise HTTPException(status.HTTP_409_CONFLICT, "only read-only bridges are accepted")
    now = utcnow()
    ex_id = _exchange_id(s)
    names = {sym.name for sym in snap.symbols}
    pids: dict[str, int] = {sym.name: _store_symbol(s, ex_id, sym, now) for sym in snap.symbols}
    written = 0
    for name, rows in snap.bars.items():
        if name in pids and re.match(SYMBOL_RE, name):
            written += repo.upsert_candles(s, pids[name], "1d",
                                           _closed_bars(rows, snap.server_utc_offset_hours, now),
                                           source="mt5")
    have = dict(s.execute(select(models.MarketCandle.pair_id, func.count()).where(
        models.MarketCandle.pair_id.in_(list(pids.values())),
        models.MarketCandle.timeframe == "1d").group_by(models.MarketCandle.pair_id)).all())
    need = sorted(n for n, pid in pids.items() if have.get(pid, 0) < HISTORY_WANTED)
    bridge.last_seen_at = now
    acct = snap.account
    summary = {
        "at": now.isoformat(), "bridge": bridge.name, "bridge_version": snap.bridge_version,
        "read_only": True, "server": acct.server[:64], "company": acct.company[:128],
        "account": f"***{str(acct.login)[-3:]}", "trade_mode": acct.trade_mode,
        "currency": acct.currency, "balance": str(acct.balance), "equity": str(acct.equity),
        "margin": str(acct.margin), "margin_free": str(acct.margin_free),
        "leverage": acct.leverage, "server_utc_offset_hours": snap.server_utc_offset_hours,
        "symbols": [{"name": x.name, "base_name": x.base_name.upper(),
                     "class": classify(x.base_name)[0], "bid": str(x.bid) if x.bid else None,
                     "ask": str(x.ask) if x.ask else None, "spread_points": x.spread_points,
                     "contract_size": str(x.contract_size), "volume_min": str(x.volume_min),
                     "swap_long": str(x.swap_long) if x.swap_long is not None else None,
                     "swap_short": str(x.swap_short) if x.swap_short is not None else None}
                    for x in snap.symbols],
        "unresolved": snap.unresolved[:50],
        "positions": [{"symbol": p.symbol, "side": p.side, "volume": str(p.volume),
                       "price_open": str(p.price_open), "profit": str(p.profit),
                       "swap": str(p.swap)} for p in snap.positions],
    }
    await redis.set(STATUS_KEY, json.dumps(summary), ex=86400)
    log.info("mt5_snapshot", bridge=bridge.name, symbols=len(names), bars_written=written,
             trade_mode=acct.trade_mode)
    return {"ok": True, "bars_written": written, "need_history": need}
