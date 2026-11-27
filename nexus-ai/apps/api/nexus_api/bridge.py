"""Read-only broker bridge endpoint (Exness MetaTrader 5 first).

The bridge (``apps/mt5_bridge``) runs on the owner's Windows machine next to a logged-in
MT5 terminal. It connects OUT to NEXUS; NEXUS never connects to the owner's computer, and
the MT5 password never leaves the terminal.

Each snapshot carries contract details, closed daily bars, quotes and an account summary,
and (bridge 1.1+) closed M5 and H1 bars of the forex majors and gold.
NEXUS stores:
* contract details in ``trading_pairs`` (exchange ``exness``, with asset class, type and
  contract size);
* closed daily, M5 and H1 bars in ``market_candles`` (source ``mt5``), converted from the
  broker's server time to UTC and checked first (``nexus_market.fx_bars``);
* a summary in Redis, with the account number masked.

There is no order endpoint: nothing here can trade.

Authentication: ``Authorization: Bearer <token>``. The token is created by the owner
(``/api/v1/bridge-tokens``), shown once, stored only as a SHA-256 hash, and revocable. The
endpoint is rate-limited per IP and has a body size limit.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_db import models, repo
from nexus_market import fx_bars
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
QUALITY_KEY = "nexus:broker:exness:bars_quality"
FULL_KEY = "nexus:broker:exness:full:{name}:{tf}"
FULL_MARKER_SECONDS = 6 * 3600               # a full series arrived: no backfill request
PRUNE_KEY = "nexus:broker:exness:prune"
PRUNE_EVERY = 3600
FX = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "SGD",
      "HKD", "ZAR", "MXN", "TRY", "PLN", "CZK", "HUF", "CNH", "THB", "ILS"}
METALS_ENERGY = ("XAU", "XAG", "XPT", "XPD", "USOIL", "UKOIL", "XNG", "XBR", "XTI")
SYMBOL_RE = r"^[A-Za-z0-9._#-]{1,32}$"

# Research history (see HistoryChunk): M5 by month, H1 by year, D1 in one piece.
HISTORY_RATE_LIMIT = 900                     # requests per IP per 10 minutes
HISTORY_MAX_BODY = 3_000_000
HISTORY_MAX_ROWS = 15_000
HISTORY_MAX_BYTES = 60_000_000               # total stored, to protect the database plan
HISTORY_TF = {"M5": (300, r"^\d{4}-(0[1-9]|1[0-2])$"), "H1": (3600, r"^\d{4}$"),
              "D1": (86400, r"^all$")}
HISTORY_VENUE = "exness_mt5"


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


class IntradayIn(BaseModel):
    name: str = Field(pattern=SYMBOL_RE)
    timeframe: str = Field(pattern=r"^(M5|H1)$")
    full: bool = False                        # the whole series (a backfill), not recent bars
    rows: list[list[float]] = Field(max_length=12_000)


class SnapshotIn(BaseModel):
    bridge_version: str = Field(max_length=16)
    read_only: bool
    server_utc_offset_hours: int = Field(ge=-14, le=14)
    account: AccountIn
    symbols: list[SymbolIn] = Field(max_length=200)
    bars: dict[str, list[list[float]]] = Field(default_factory=dict)   # name -> [t,o,h,l,c,v]
    positions: list[PositionIn] = Field(default_factory=list, max_length=500)
    unresolved: list[str] = Field(default_factory=list, max_length=200)
    intraday: list[IntradayIn] = Field(default_factory=list, max_length=40)


async def _authenticate(request: Request, s: Session, redis: Any, bucket: str,
                        limit: int) -> models.BrokerBridge:
    ip = client_ip(request)
    key = f"nexus:rl:{bucket}:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, RATE_WINDOW)
    if n > limit:
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


async def bridge_auth(request: Request, s: Session = Depends(db),
                      redis: Any = Depends(redis_dep)) -> models.BrokerBridge:
    return await _authenticate(request, s, redis, "bridge", RATE_LIMIT)


async def history_auth(request: Request, s: Session = Depends(db),
                       redis: Any = Depends(redis_dep)) -> models.BrokerBridge:
    """A separate, larger budget: a full history export is a few hundred requests."""
    return await _authenticate(request, s, redis, "bridge_history", HISTORY_RATE_LIMIT)


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
    return fx_bars.check_bars(rows, offset_h, now, 86400, align=False).bars


def store_intraday(s: Session, items: list[IntradayIn], pids: dict[str, int],
                   bases: dict[str, str], offset_h: int, now: datetime,
                   recently_full: set[str]) -> tuple[int, dict[str, Any], list[str]]:
    """Check and store M5/H1 bars (blocking database work: run in a thread). Returns
    (bars written, validation counts by "<base>/<TF>", series to backfill "<name>/<TF>")."""
    ids = sorted({pids[i.name] for i in items if i.name in pids})
    before = fx_bars.stored_state(s, ids, now)
    written, quality, need = 0, {}, []
    for item in items:
        if item.name not in pids:
            continue
        tf, secs = fx_bars.MT5_TF[item.timeframe]
        checked = fx_bars.check_bars(item.rows, offset_h, now, secs)
        written += repo.upsert_candles(s, pids[item.name], tf, checked.bars, source="mt5")
        quality[f"{bases[item.name]}/{item.timeframe}"] = {
            "at": now.isoformat(), "received": len(item.rows), "stored": len(checked.bars),
            "forming": checked.forming, "rejected": checked.rejected, "jumps": checked.jumps,
            "full": item.full}
        stored, newest = before.get((pids[item.name], tf), (0, None))
        key = f"{item.name}/{item.timeframe}"
        if fx_bars.need_backfill(stored, newest,
                                 checked.bars[0].open_time if checked.bars else None, tf,
                                 recently_full=item.full or key in recently_full):
            need.append(key)
    need.sort(key=lambda k: (not k.endswith("/H1"), k))      # H1 first: strategies use it
    return written, quality, need


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
    intraday_written, need_intraday = 0, list[str]()
    if snap.intraday:
        bases = {sym.name: sym.base_name.upper() for sym in snap.symbols}
        recently_full = set()
        for item in snap.intraday:
            key = FULL_KEY.format(name=item.name, tf=item.timeframe)
            if item.full:
                await redis.set(key, "1", ex=FULL_MARKER_SECONDS)
            elif await redis.exists(key):
                recently_full.add(f"{item.name}/{item.timeframe}")
        intraday_written, quality, need_intraday = await asyncio.to_thread(
            store_intraday, s, snap.intraday, pids, bases, snap.server_utc_offset_hours, now,
            recently_full)
        old = await redis.get(QUALITY_KEY)
        merged = {**(json.loads(old) if old else {}), **quality}
        await redis.set(QUALITY_KEY, json.dumps(merged), ex=7 * 86400)
        if await redis.set(PRUNE_KEY, "1", ex=PRUNE_EVERY, nx=True):
            removed = await asyncio.to_thread(fx_bars.prune, s, list(pids.values()), now)
            if removed:
                log.info("mt5_intraday_pruned", rows=removed)
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
             intraday_written=intraday_written, need_intraday=len(need_intraday),
             trade_mode=acct.trade_mode)
    return {"ok": True, "bars_written": written, "need_history": need,
            "intraday_written": intraday_written, "need_intraday": need_intraday}


# ----------------------------------------------------------------------- history
class HistoryIn(BaseModel):
    bridge_version: str = Field(max_length=16)
    read_only: bool
    server_utc_offset_hours: int = Field(ge=-14, le=14)
    symbol: str = Field(pattern=SYMBOL_RE)               # broker symbol, e.g. EURUSDm
    base_name: str = Field(pattern=r"^[A-Z]{6}$")        # FX pairs only, e.g. EURUSD
    timeframe: str = Field(pattern=r"^(M5|H1|D1)$")
    period: str = Field(pattern=r"^(\d{4}(-\d{2})?|all)$")
    rows: list[list[float]] = Field(max_length=HISTORY_MAX_ROWS)   # [t, o, h, l, c, v]


def _in_period(server_ts: int, period: str) -> bool:
    if period == "all":
        return True
    t = datetime.fromtimestamp(server_ts, tz=UTC)      # server time read as wall clock
    return f"{t.year:04d}" == period if len(period) == 4 else \
        f"{t.year:04d}-{t.month:02d}" == period


def history_csv(rows: list[list[float]], timeframe: str, period: str, offset_h: int,
                now: datetime) -> tuple[str, int, datetime, datetime]:
    """Closed, valid bars of the period as UTC CSV. Raises ValueError if none remain.

    Server time -> UTC by the measured offset; the forming bar (open + length > now),
    bars outside the period (by server time), non-finite or inconsistent rows and
    duplicates are dropped."""
    length = timedelta(seconds=HISTORY_TF[timeframe][0])
    kept: dict[datetime, str] = {}
    for r in rows:
        if len(r) < 5 or not all(math.isfinite(x) for x in r[:5]) or min(r[1:5]) <= 0:
            continue
        o, h, lo, c = r[1:5]
        if h < max(o, c) or lo > min(o, c) or not _in_period(int(r[0]), period):
            continue
        t = datetime.fromtimestamp(int(r[0]), tz=UTC) - timedelta(hours=offset_h)
        if t + length > now:
            continue
        v = r[5] if len(r) > 5 and math.isfinite(r[5]) and r[5] >= 0 else 0
        kept[t] = f"{t:%Y-%m-%dT%H:%M:%SZ},{o!r},{h!r},{lo!r},{c!r},{int(v)}"
    if not kept:
        raise ValueError("no closed, valid bars in this chunk")
    times = sorted(kept)
    body = "\n".join(kept[t] for t in times)
    return body, len(times), times[0], times[-1]


@router.post("/mt5/history/manifest")
async def mt5_history_manifest(s: Session = Depends(db),
                               bridge: models.BrokerBridge = Depends(history_auth)
                               ) -> dict[str, Any]:
    """What NEXUS already holds, so the bridge sends only missing or unfinished chunks."""
    rows = s.execute(select(models.HistoryChunk.symbol, models.HistoryChunk.timeframe,
                            models.HistoryChunk.period, models.HistoryChunk.bar_count,
                            models.HistoryChunk.last_open).where(
        models.HistoryChunk.venue == HISTORY_VENUE)).all()
    used = s.scalar(select(func.coalesce(func.sum(func.octet_length(models.HistoryChunk.data)),
                                         0))) or 0
    return {"chunks": [{"symbol": r[0], "timeframe": r[1], "period": r[2], "bars": r[3],
                        "last_open": r[4].isoformat()} for r in rows],
            "used_bytes": int(used), "limit_bytes": HISTORY_MAX_BYTES}


@router.post("/mt5/history")
async def mt5_history(request: Request, s: Session = Depends(db),
                      bridge: models.BrokerBridge = Depends(history_auth)) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > HISTORY_MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "chunk too large")
    try:
        chunk = HistoryIn.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid history chunk: {str(exc)[:300]}") from None
    if not chunk.read_only:
        raise HTTPException(status.HTTP_409_CONFLICT, "only read-only bridges are accepted")
    if not re.match(HISTORY_TF[chunk.timeframe][1], chunk.period):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "period must be YYYY-MM for M5, YYYY for H1, all for D1")
    if classify(chunk.base_name)[0] != "forex":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "forex pairs only")
    now = utcnow()
    try:
        body, n, first, last = history_csv(chunk.rows, chunk.timeframe, chunk.period,
                                           chunk.server_utc_offset_hours, now)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    data = gzip.compress(("time,open,high,low,close,volume\n" + body + "\n").encode(),
                         mtime=0)
    key = (models.HistoryChunk.venue == HISTORY_VENUE,
           models.HistoryChunk.symbol == chunk.base_name,
           models.HistoryChunk.timeframe == chunk.timeframe,
           models.HistoryChunk.period == chunk.period)
    row = s.scalar(select(models.HistoryChunk).where(*key).with_for_update())
    others = s.scalar(select(func.coalesce(func.sum(func.octet_length(
        models.HistoryChunk.data)), 0)).where(models.HistoryChunk.id != (row.id if row else -1)))
    if int(others or 0) + len(data) > HISTORY_MAX_BYTES:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE,
                            "research history storage is full")
    if row is None:
        row = models.HistoryChunk(venue=HISTORY_VENUE, symbol=chunk.base_name,
                                  timeframe=chunk.timeframe, period=chunk.period)
        s.add(row)
    row.broker_symbol = chunk.symbol
    row.bar_count, row.first_open, row.last_open = n, first, last
    row.server_utc_offset_hours = chunk.server_utc_offset_hours
    row.sha256 = hashlib.sha256(data).hexdigest()
    row.data, row.bridge_id, row.received_at = data, bridge.id, now
    bridge.last_seen_at = now
    s.flush()
    log.info("mt5_history", bridge=bridge.name, symbol=chunk.base_name,
             timeframe=chunk.timeframe, period=chunk.period, bars=n)
    return {"ok": True, "bars_stored": n, "first_open": first.isoformat(),
            "last_open": last.isoformat()}


def history_text(s: Session, symbol: str, timeframe: str) -> str | None:
    """All stored chunks of one series as a single UTC CSV (header once), in time order."""
    chunks = s.scalars(select(models.HistoryChunk).where(
        models.HistoryChunk.venue == HISTORY_VENUE, models.HistoryChunk.symbol == symbol,
        models.HistoryChunk.timeframe == timeframe).order_by(
        models.HistoryChunk.first_open)).all()
    if not chunks:
        return None
    lines: dict[str, str] = {}
    for c in chunks:
        for line in gzip.decompress(c.data).decode().splitlines()[1:]:
            if line:
                lines[line.split(",", 1)[0]] = line
    return "time,open,high,low,close,volume\n" + "\n".join(lines[k] for k in sorted(lines)) \
        + "\n"
