"""Exness MetaTrader 5 DEMO trading: NEXUS sends order instructions, and the owner's
Windows demo trader (``apps/mt5_bridge/nexus_mt5_demo_trader.py``) carries them out.

**Demo accounts only, enforced in two places:**
* this endpoint refuses (403, audited) any account that is not ``trade_mode == "demo"``;
* the demo trader refuses to run on a non-demo account.

Forex and commodities have no LIVE mode in NEXUS at all.

**New entries need every one of these:**
* the instrument's market switch at PAPER (forex for EURUSD, commodities for XAUUSD),
  which only the owner can set;
* the kill switch not engaged;
* the demo account within NEXUS's limits: 1% daily loss, 2.5% weekly loss, and the 5%
  drawdown halt, which stays until the owner resets it.

Exits and reductions are always allowed.

The strategies are in ``nexus_strategies.exness_demo``. Neither passed its test; they
run here to be watched, not to make money.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, status
from nexus_common.logging import get_logger
from nexus_common.markets import Autonomy, Market, effective
from nexus_common.timeutil import utcnow
from nexus_db import models, repo
from nexus_risk.kill_switch import KillSwitch
from nexus_strategies import exness_demo as plan
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from nexus_api.bridge import SYMBOL_RE, AccountIn, bridge_auth
from nexus_api.deps import db, redis_dep

log = get_logger("nexus.api.mt5_demo")

router = APIRouter(prefix="/api/v1/bridge/mt5/demo", tags=["bridge"])

STATUS_KEY = "nexus:broker:exness:demo"
HALT_KEY = "nexus:mt5demo:halt"
PEAK_KEY = "nexus:mt5demo:peak"
MAX_BODY = 1_500_000
DAILY, WEEKLY, DRAWDOWN = 0.01, 0.025, 0.05
INSTRUMENTS = {"XAUUSD": Market.COMMODITIES, "EURUSD": Market.FOREX}
TF_SECONDS = {"D1": 86400, "H1": 3600}


class DemoSymbol(BaseModel):
    name: str = Field(pattern=SYMBOL_RE)
    base_name: str = Field(pattern=r"^(XAUUSD|EURUSD)$")
    point: float = Field(gt=0)
    contract_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)


class DemoPosition(BaseModel):
    ticket: int
    symbol: str = Field(pattern=SYMBOL_RE)
    side: str = Field(pattern=r"^(buy|sell)$")
    volume: float = Field(gt=0)
    price_open: float = Field(gt=0)
    sl: float = 0.0
    tp: float = 0.0
    time: int                                          # broker server time, seconds
    magic: int
    comment: str = Field(default="", max_length=64)


class DemoResult(BaseModel):
    client_id: str = Field(max_length=40, pattern=r"^[A-Za-z0-9]+$")
    retcode: int
    message: str = Field(default="", max_length=200)


class DemoSyncIn(BaseModel):
    bridge_version: str = Field(max_length=16)
    server_utc_offset_hours: int = Field(ge=-14, le=14)
    account: AccountIn
    symbols: list[DemoSymbol] = Field(max_length=4)
    positions: list[DemoPosition] = Field(default_factory=list, max_length=100)
    bars: dict[str, dict[str, list[list[float]]]] = Field(default_factory=dict)
    results: list[DemoResult] = Field(default_factory=list, max_length=50)


def _closed(rows: list[list[float]], tf: str, offset_h: int, now: datetime) -> pd.DataFrame:
    """Closed bars only, broker server time -> UTC; invalid rows dropped."""
    out = []
    for r in rows[-3000:]:
        if len(r) < 5 or min(r[1:5]) <= 0 or r[2] < max(r[1], r[4]) or r[3] > min(r[1], r[4]):
            continue
        t = datetime.fromtimestamp(int(r[0]), tz=UTC) - timedelta(hours=offset_h)
        if t + timedelta(seconds=TF_SECONDS[tf]) <= now:
            out.append((t, r[1], r[2], r[3], r[4]))
    df = pd.DataFrame(out, columns=["time", "open", "high", "low", "close"])
    return df.drop_duplicates("time").set_index("time").sort_index()


async def _limits(redis: Any, equity: float, now: datetime) -> dict[str, Any]:
    """Daily, weekly and drawdown limits for the demo account (state in Redis)."""
    day, week = now.strftime("%Y-%m-%d"), f"{now.isocalendar().year}-{now.isocalendar().week}"
    await redis.set(f"nexus:mt5demo:day:{day}", equity, nx=True, ex=3 * 86400)
    await redis.set(f"nexus:mt5demo:week:{week}", equity, nx=True, ex=10 * 86400)
    day_start = float(await redis.get(f"nexus:mt5demo:day:{day}"))
    week_start = float(await redis.get(f"nexus:mt5demo:week:{week}"))
    peak = max(float(await redis.get(PEAK_KEY) or equity), equity)
    await redis.set(PEAK_KEY, peak)
    halt = await redis.get(HALT_KEY)
    new_halt = None
    if halt is None and equity <= peak * (1 - DRAWDOWN):
        new_halt = {"reason": f"demo equity {equity:.2f} is {DRAWDOWN:.0%} below its peak "
                              f"{peak:.2f}", "at": now.isoformat()}
        await redis.set(HALT_KEY, json.dumps(new_halt))
        halt = json.dumps(new_halt)
    reasons = []
    if halt is not None:
        reasons.append("drawdown halt (owner reset required)")
    if equity <= day_start * (1 - DAILY):
        reasons.append("daily loss limit")
    if equity <= week_start * (1 - WEEKLY):
        reasons.append("weekly loss limit")
    return {"blocked": reasons, "new_halt": new_halt, "peak": peak, "day_start": day_start,
            "week_start": week_start}


@router.post("/sync")
async def demo_sync(request: Request, s: Session = Depends(db),
                    redis: Any = Depends(redis_dep),
                    bridge: models.BrokerBridge = Depends(bridge_auth)) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "sync too large")
    try:
        body = DemoSyncIn.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid demo sync: {str(exc)[:300]}") from None
    actor = f"bridge:{bridge.id}"
    if body.account.trade_mode != "demo":
        repo.record_audit(s, actor=actor, action="mt5_demo_refused", target_type="broker_bridge",
                          target_id=str(bridge.id),
                          details={"trade_mode": body.account.trade_mode})
        s.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "NEXUS sends orders only to DEMO accounts; this account is "
                            f"{body.account.trade_mode}")
    now = utcnow()
    rows = repo.market_autonomy_db_state(s)
    killed = await KillSwitch(redis).status() is not None or \
        repo.kill_switch_db_state(s) is not None
    lim = await _limits(redis, float(body.account.equity), now)
    if lim["new_halt"]:
        repo.record_audit(s, actor=actor, action="mt5_demo_halted", details=lim["new_halt"])
    instructions: list[dict[str, Any]] = []
    gates: dict[str, Any] = {}
    equity = float(body.account.equity)
    by_name = {x.name: x for x in body.symbols}
    for sym in body.symbols:
        market = INSTRUMENTS[sym.base_name]
        auto = effective(market, rows)
        blocked = list(lim["blocked"])
        if auto is not Autonomy.PAPER:
            blocked.append(f"{market.value} switch is {auto.value} (PAPER needed)")
        if killed:
            blocked.append("kill switch engaged")
        gates[sym.base_name] = {"market": market.value, "autonomy": auto.value,
                                "entries_allowed": not blocked, "blocked_by": blocked}
        spec = plan.Spec(sym.name, sym.contract_size, sym.volume_min, sym.volume_step,
                         sym.volume_max, sym.point)
        mine = [p.model_dump() | {"time_utc": datetime.fromtimestamp(
                    p.time, tz=UTC) - timedelta(hours=body.server_utc_offset_hours)}
                for p in body.positions if p.magic == plan.MAGIC and p.symbol == sym.name]
        bars = body.bars.get(sym.name, {})
        if sym.base_name == "XAUUSD":
            d1 = _closed(bars.get("D1", []), "D1", body.server_utc_offset_hours, now)
            instructions += plan.gold_plan(d1["close"] if len(d1) else pd.Series(dtype=float),
                                           sym.bid, sym.ask, equity, mine, spec, not blocked,
                                           now.strftime("%Y%m%d"))
        else:
            h1 = _closed(bars.get("H1", []), "H1", body.server_utc_offset_hours, now)
            instructions += plan.eurusd_plan(h1, sym.bid, sym.ask, equity,
                                             mine[0] if mine else None, spec, not blocked,
                                             pd.Timestamp(now))
    for ins in instructions:
        ins["magic"] = plan.MAGIC
        if not re.fullmatch(r"[A-Za-z0-9]{1,31}", ins["client_id"]):
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "bad client id")
    if instructions:
        repo.record_audit(s, actor=actor, action="mt5_demo_instructions",
                          target_type="broker_bridge", target_id=str(bridge.id),
                          details={"instructions": instructions, "equity": equity})
    bridge.last_seen_at = now
    acct = body.account
    status_doc = {
        "at": now.isoformat(), "bridge": bridge.name, "demo_only": True,
        "account": f"***{str(acct.login)[-3:]}", "trade_mode": acct.trade_mode,
        "server": acct.server[:64], "currency": acct.currency, "balance": str(acct.balance),
        "equity": str(acct.equity), "limits": {k: lim[k] for k in ("blocked", "peak",
                                                                    "day_start", "week_start")},
        "gates": gates, "kill_switch": killed,
        "positions": [p.model_dump() for p in body.positions if p.magic == plan.MAGIC],
        "instructions": instructions, "results": [r.model_dump() for r in body.results],
        "symbols": {k: {"bid": v.bid, "ask": v.ask, "contract_size": v.contract_size,
                        "base_name": v.base_name} for k, v in by_name.items()}}
    await redis.set(STATUS_KEY, json.dumps(status_doc, default=str), ex=86400)
    log.info("mt5_demo_sync", bridge=bridge.name, instructions=len(instructions),
             gates={k: v["entries_allowed"] for k, v in gates.items()})
    return {"ok": True, "demo_only": True, "instructions": instructions, "gates": gates}
