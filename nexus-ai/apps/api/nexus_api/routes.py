"""Dashboard API. Every route requires an authenticated session; mutations also pass the
CSRF guard, are owner-only, are audit-logged, and sensitive ones require TOTP.

Monetary values are returned as exact decimal strings. Missing data is ``null``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from nexus_common import events
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models, repo
from nexus_exchange import CAPABILITIES
from nexus_execution.live_gate import live_gate_problems
from nexus_execution.reports import portfolio_report
from nexus_risk.kill_switch import KillSwitch
from nexus_security import CredentialCipher, KeyRing, key_fingerprint
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from nexus_api import binance_ban
from nexus_api.auth import check_totp
from nexus_api.deps import Viewer, csrf_guard, current_viewer, db, owner, redis_dep, ser, settings_dep

router = APIRouter(prefix="/api/v1", dependencies=[Depends(csrf_guard),
                                                   Depends(current_viewer)])
Mode = Literal["paper", "live"]


def _portfolio(s: Session, mode: str) -> models.Portfolio | None:
    return s.scalar(select(models.Portfolio).where(models.Portfolio.mode == mode,
                                                   models.Portfolio.name == "main"))


def _rows(objs: Any, cols: list[str]) -> list[dict[str, Any]]:
    return [{c: ser(getattr(o, c)) for c in cols} for o in objs]


async def _health(redis: Any) -> dict[str, Any]:
    out = {}
    for code in ("binance", "kraken", "coinbase"):
        raw = await redis.get(events.health_key(code))
        out[code] = json.loads(raw) if raw else None
    return out


# ------------------------------------------------------------------ overview
@router.get("/overview")
async def overview(mode: Mode = "paper", s: Session = Depends(db), redis: Any = Depends(redis_dep),
                   settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    p = _portfolio(s, mode)
    report = portfolio_report(s, p.id) if p else None
    signals = s.scalars(select(models.TradingSignal).where(models.TradingSignal.mode == mode)
                        .order_by(desc(models.TradingSignal.id)).limit(5)).all()
    open_pos = s.scalar(select(func.count()).select_from(models.Position).where(
        models.Position.mode == mode, models.Position.status != "closed")) or 0
    strategies = s.scalars(select(models.Strategy)).all()
    model = s.scalar(select(models.AiModel).order_by(desc(models.AiModel.id)).limit(1))
    return {
        "mode": mode, "config": settings.safe_summary(), "report": report,
        "open_positions": open_pos, "exchanges": await _health(redis),
        "kill_switch": await _kill_status(s, redis),
        "recent_signals": _rows(signals, ["id", "status", "side", "timeframe", "bar_time",
                                          "entry_price", "confidence", "rejection_reason"]),
        "strategies": [{"code": x.code, "enabled": x.is_enabled,
                        "validated": bool(((x.parameters or {}).get("validation") or {})
                                          .get("passed"))} for x in strategies],
        "latest_model": None if model is None else {"name": model.name, "version": model.version,
                                                    "status": model.status},
    }


# ------------------------------------------------------------------ portfolio
@router.get("/portfolio")
def portfolio(mode: Mode = "paper", s: Session = Depends(db)) -> dict[str, Any]:
    p = _portfolio(s, mode)
    if p is None:
        return {"mode": mode, "portfolio": None, "balances": [], "equity_curve": []}
    bal = s.execute(select(models.Balance, models.Exchange.code).join(models.Exchange)
                    .where(models.Balance.portfolio_id == p.id)
                    .order_by(models.Exchange.code, models.Balance.asset)).all()
    curve = s.scalars(select(models.PerformanceSnapshot).where(
        models.PerformanceSnapshot.portfolio_id == p.id)
        .order_by(models.PerformanceSnapshot.captured_at).limit(5000)).all()
    return {"mode": mode, "portfolio": portfolio_report(s, p.id),
            "balances": [{"exchange": code, "asset": b.asset, "free": ser(b.free),
                          "locked": ser(b.locked), "source": b.source,
                          "synced_at": ser(b.synced_at)} for b, code in bal],
            "equity_curve": [{"t": ser(c.captured_at), "equity": ser(c.equity),
                              "drawdown": ser(c.drawdown)} for c in curve]}


# ------------------------------------------------------------------ exchanges / markets
@router.get("/exchanges")
async def exchanges(s: Session = Depends(db), redis: Any = Depends(redis_dep)) -> list[dict[str, Any]]:
    health = await _health(redis)
    out = []
    for ex in s.scalars(select(models.Exchange).order_by(models.Exchange.id)):
        caps = CAPABILITIES.get(ex.code)  # type: ignore[call-overload]
        pairs = s.scalars(select(models.TradingPair).where(models.TradingPair.exchange_id == ex.id)).all()
        creds = s.scalars(select(models.ExchangeCredential).where(
            models.ExchangeCredential.exchange_id == ex.id,
            models.ExchangeCredential.revoked_at.is_(None))).all()
        out.append({
            "code": ex.code, "name": ex.name, "health": health.get(ex.code),
            "capabilities": None if caps is None else {
                "client_order_lookup": caps.client_order_lookup,
                "sandbox_available": caps.sandbox_available,
                "withdrawal_permission_check": caps.withdrawal_permission_check,
                "aggregated_timeframes": [list(x) for x in caps.aggregated_timeframes],
                "ohlcv_history_bars": caps.ohlcv_history_bars, "notes": list(caps.notes)},
            "pairs": _rows(pairs, ["symbol", "exchange_symbol", "price_tick", "amount_step",
                                   "min_amount", "min_notional", "metadata_synced_at"]),
            "credentials": [{"id": c.id, "label": c.label, "environment": c.environment,
                             "fingerprint": c.key_fingerprint,
                             "withdrawal_disabled_verified": c.withdrawal_disabled_verified,
                             "created_at": ser(c.created_at)} for c in creds],
        })
    return out


TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


async def _public_get(settings: Settings, path: str, params: dict[str, Any]) -> Any:
    import httpx

    base = settings.binance_public_api_url or "https://api.binance.com/api/v3"
    async with httpx.AsyncClient(timeout=15, trust_env=True) as client:
        r = await client.get(f"{base}/{path}", params=params)
        r.raise_for_status()
        return r.json()


@router.get("/markets/candles")
async def candles(exchange: str, symbol: str,
                  timeframe: Literal["5m", "15m", "1h", "4h", "1d"] = "1h",
                  limit: int = Query(300, ge=10, le=1000), s: Session = Depends(db),
                  settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Stored closed candles; for Binance, falls back to Binance's public klines (closed
    bars only) when nothing fresh is stored, e.g. while no market-data worker runs."""
    rows: list[Any] = []
    try:
        rows = repo.load_candles(s, repo.pair_id(s, exchange, symbol), timeframe, limit=limit)
    except LookupError as exc:
        if exchange != "binance":
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"unknown market {exchange} {symbol}") from exc
    now = utcnow()
    fresh = bool(rows) and (now - rows[-1].open_time).total_seconds() < 3 * TF_SECONDS[timeframe]
    if fresh or exchange != "binance":
        return {"exchange": exchange, "symbol": symbol, "timeframe": timeframe,
                "source": "database",
                "candles": [{"t": int(c.open_time.timestamp()), "o": str(c.open),
                             "h": str(c.high), "l": str(c.low), "c": str(c.close),
                             "v": str(c.volume)} for c in rows]}
    import httpx

    try:
        raw = await _public_get(settings, "klines", {"symbol": symbol.replace("/", ""),
                                                     "interval": timeframe, "limit": limit + 1})
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Binance public data unavailable ({type(exc).__name__})") from exc
    now_ms = now.timestamp() * 1000
    closed = [r for r in raw if int(r[6]) < now_ms][-limit:]
    return {"exchange": exchange, "symbol": symbol, "timeframe": timeframe,
            "source": "binance public API (live)",
            "candles": [{"t": int(r[0]) // 1000, "o": r[1], "h": r[2], "l": r[3], "c": r[4],
                         "v": r[5]} for r in closed]}


@router.get("/markets/books")
async def books(redis: Any = Depends(redis_dep),
                settings: Settings = Depends(settings_dep)) -> list[dict[str, Any]]:
    """WebSocket top of book from Redis; for Binance, the public REST best bid/ask when no
    stream is running. Missing data stays missing."""
    import httpx

    out = []
    for ex in settings.enabled_exchanges:
        for sym in settings.trading_pairs:
            raw = await redis.get(events.book_key(ex.value, sym))
            data = json.loads(raw) if raw else None
            source = "websocket" if data else None
            if data is None and ex.value == "binance":
                try:
                    t = await _public_get(settings, "ticker/bookTicker",
                                          {"symbol": sym.replace("/", "")})
                    data = {"bid": t["bidPrice"], "ask": t["askPrice"], "bid_size": t["bidQty"],
                            "ask_size": t["askQty"], "ts": utcnow().isoformat()}
                    source = "binance public API (REST)"
                except (httpx.HTTPError, KeyError):
                    data = None
            out.append({"exchange": ex.value, "symbol": sym, "book": data, "source": source})
    return out


# ------------------------------------------------------------------ trading records
@router.get("/signals")
def signals(mode: Mode = "paper", limit: int = Query(100, le=500),
            s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = (select(models.TradingSignal, models.Strategy.code, models.TradingPair.symbol)
         .join(models.Strategy).join(models.TradingPair)
         .where(models.TradingSignal.mode == mode)
         .order_by(desc(models.TradingSignal.id)).limit(limit))
    return [{**{c: ser(getattr(sig, c)) for c in ("id", "mode", "side", "timeframe", "bar_time",
                                                  "entry_price", "stop_loss", "take_profit",
                                                  "confidence", "status", "rejection_reason",
                                                  "generated_at")},
             "strategy": code, "symbol": sym} for sig, code, sym in s.execute(q).all()]


@router.get("/positions")
def positions(mode: Mode = "paper", status_: str | None = Query(None, alias="status"),
              s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = (select(models.Position, models.Exchange.code, models.TradingPair.symbol)
         .join(models.Exchange, models.Exchange.id == models.Position.exchange_id)
         .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
         .where(models.Position.mode == mode).order_by(desc(models.Position.id)).limit(500))
    if status_:
        q = q.where(models.Position.status == status_)
    cols = ["id", "mode", "status", "quantity", "avg_entry_price", "cost_basis", "stop_loss",
            "take_profit", "realized_pnl", "fees_paid", "exit_reason", "opened_at", "closed_at"]
    return [{**{c: ser(getattr(p, c)) for c in cols}, "exchange": ex, "symbol": sym}
            for p, ex, sym in s.execute(q).all()]


@router.get("/orders")
def orders(mode: Mode = "paper", limit: int = Query(200, le=1000),
           s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = (select(models.Order, models.Exchange.code, models.TradingPair.symbol)
         .join(models.Exchange, models.Exchange.id == models.Order.exchange_id)
         .join(models.TradingPair, models.TradingPair.id == models.Order.pair_id)
         .where(models.Order.mode == mode).order_by(desc(models.Order.id)).limit(limit))
    cols = ["id", "mode", "client_order_id", "exchange_order_id", "side", "order_type",
            "time_in_force", "price", "amount", "filled_amount", "average_fill_price",
            "expected_price", "fee_amount", "fee_asset", "status", "purpose", "last_error",
            "created_at", "closed_at"]
    return [{**{c: ser(getattr(o, c)) for c in cols}, "exchange": ex, "symbol": sym}
            for o, ex, sym in s.execute(q).all()]


@router.get("/trades")
def trades(mode: Mode = "paper", limit: int = Query(200, le=1000),
           s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = (select(models.Trade, models.Order.side, models.Exchange.code, models.TradingPair.symbol)
         .join(models.Order, (models.Order.id == models.Trade.order_id)
               & (models.Order.mode == models.Trade.mode))
         .join(models.Exchange, models.Exchange.id == models.Order.exchange_id)
         .join(models.TradingPair, models.TradingPair.id == models.Order.pair_id)
         .where(models.Trade.mode == mode).order_by(desc(models.Trade.id)).limit(limit))
    return [{"id": t.id, "order_id": t.order_id, "trade_id": t.exchange_trade_id, "side": side,
             "price": ser(t.price), "amount": ser(t.amount), "fee": ser(t.fee),
             "fee_asset": t.fee_asset, "liquidity": t.liquidity,
             "executed_at": ser(t.executed_at), "exchange": ex, "symbol": sym, "mode": t.mode}
            for t, side, ex, sym in s.execute(q).all()]


@router.get("/performance")
def performance(mode: Mode = "paper", s: Session = Depends(db)) -> dict[str, Any]:
    p = _portfolio(s, mode)
    runs = s.execute(select(models.BacktestRun, models.Strategy.code).outerjoin(models.Strategy)
                     .order_by(desc(models.BacktestRun.id)).limit(20)).all()
    return {"mode": mode, "report": portfolio_report(s, p.id) if p else None,
            "backtests": [{"id": r.id, "strategy": code, "walk_forward": r.is_walk_forward,
                           "data_start": ser(r.data_start), "data_end": ser(r.data_end),
                           "fee_bps": ser(r.fee_bps), "slippage_bps": ser(r.slippage_bps),
                           "summary": (r.metrics or {}).get("summary"),
                           "passed": (r.metrics or {}).get("passed"),
                           "failures": (r.metrics or {}).get("failures"),
                           "completed_at": ser(r.completed_at)} for r, code in runs]}


# ------------------------------------------------------------------ strategies / models
@router.get("/strategies")
def strategies(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [{"id": x.id, "code": x.code, "name": x.name, "enabled": x.is_enabled,
             "parameters": x.parameters, "updated_at": ser(x.updated_at)}
            for x in s.scalars(select(models.Strategy).order_by(models.Strategy.id))]


class StrategyToggle(BaseModel):
    enabled: bool


@router.patch("/strategies/{code}", dependencies=[Depends(owner)])
def toggle_strategy(code: str, body: StrategyToggle, s: Session = Depends(db),
                    viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    row = s.scalar(select(models.Strategy).where(models.Strategy.code == code))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown strategy")
    passed = bool(((row.parameters or {}).get("validation") or {}).get("passed"))
    if body.enabled and not passed:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "strategy has not passed out-of-sample validation; cannot enable")
    row.is_enabled = body.enabled
    repo.record_audit(s, actor=viewer.actor, action="strategy_toggled", user_id=viewer.user.id,
                      target_type="strategy", target_id=code, details={"enabled": body.enabled})
    return {"code": code, "enabled": row.is_enabled}


@router.get("/models")
def ai_models(s: Session = Depends(db)) -> list[dict[str, Any]]:
    out = []
    for m in s.scalars(select(models.AiModel).order_by(desc(models.AiModel.id)).limit(50)):
        evals = s.scalars(select(models.ModelEvaluation).where(
            models.ModelEvaluation.model_id == m.id).order_by(models.ModelEvaluation.id)).all()
        out.append({"id": m.id, "name": m.name, "version": m.version, "algorithm": m.algorithm,
                    "status": m.status, "created_at": ser(m.created_at),
                    "promoted_at": ser(m.promoted_at), "artifact": bool(m.artifact_sha256),
                    "features": len((m.feature_set or {}).get("columns", [])),
                    "evaluations": [{"type": e.evaluation_type, "passed": e.passed,
                                     "failures": e.failure_reasons,
                                     "summary": (e.metrics or {}).get("summary"),
                                     "windows": (e.metrics or {}).get("windows"),
                                     "period_start": ser(e.period_start),
                                     "period_end": ser(e.period_end)} for e in evals]})
    return out


class Totp(BaseModel):
    totp: str = Field(min_length=6, max_length=6)


@router.post("/models/{model_id}/activate", dependencies=[Depends(owner)])
async def activate_model(model_id: int, body: Totp, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                         viewer: Viewer = Depends(current_viewer),
                         settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    from nexus_ai.registry import activate
    await check_totp(viewer, body.totp, settings, redis)
    try:
        activate(s, model_id, actor=viewer.actor, now=utcnow())
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"ok": True}


# ------------------------------------------------------------------ risk
@router.get("/risk")
async def risk(mode: Mode = "paper", s: Session = Depends(db), redis: Any = Depends(redis_dep),
               settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    p = _portfolio(s, mode)
    ev = s.scalars(select(models.RiskEvent).where(models.RiskEvent.mode == mode)
                   .order_by(desc(models.RiskEvent.id)).limit(100)).all()
    last = None if p is None else s.scalar(select(models.PerformanceSnapshot).where(
        models.PerformanceSnapshot.portfolio_id == p.id)
        .order_by(desc(models.PerformanceSnapshot.captured_at)).limit(1))
    from nexus_execution import protection
    from nexus_risk.streak import DEFAULT_POLICY, risk_multiplier

    reset_at = await protection.streak_reset_at(redis)
    streak = None if p is None else protection.streak_state(s, p.id, reset_at)
    return {"mode": mode, "limits": {k: str(v) for k, v in settings.risk.model_dump().items()},
            "kill_switch": await _kill_status(s, redis),
            "loss_streak": None if streak is None else {
                "consecutive_losses": streak.losses, "paused": streak.paused,
                "risk_multiplier": str(risk_multiplier(streak)),
                "reduce_after": DEFAULT_POLICY.reduce_after,
                "pause_after": DEFAULT_POLICY.pause_after,
                "reset_at": ser(reset_at)},
            "current": None if last is None else {
                "equity": ser(last.equity), "peak_equity": ser(last.peak_equity),
                "drawdown": ser(last.drawdown), "exposure": ser(last.exposure),
                "exposure_pct": ser(last.exposure / last.equity) if last.equity else None,
                "open_positions": last.open_positions, "as_of": ser(last.captured_at)},
            "events": _rows(ev, ["id", "event_type", "severity", "details", "created_at"])}


async def _kill_status(s: Session, redis: Any) -> dict[str, Any] | None:
    """Redis flag, or the durable audit record if Redis lost it (shown as engaged)."""
    live = await KillSwitch(redis).status()
    return live if live is not None else repo.kill_switch_db_state(s)


CLOSE_CONFIRM = {"paper": "CLOSE PAPER POSITIONS", "live": "CLOSE ALL LIVE POSITIONS"}


class ClosePositionsBody(BaseModel):
    mode: Mode
    confirm: str = Field(max_length=64)
    totp: str | None = Field(default=None, max_length=6)


@router.get("/risk/kill-switch/state", dependencies=[Depends(owner)])
async def kill_state(s: Session = Depends(db), redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Kill-switch status with the open positions and resting orders per mode (from the
    database, which the trader reconciles with the exchange every cycle)."""
    from nexus_execution.trader import close_all_key

    out: dict[str, Any] = {"kill_switch": await _kill_status(s, redis), "modes": {}}
    for mode in ("paper", "live"):
        pos = s.execute(select(models.Position, models.TradingPair.symbol)
                        .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
                        .where(models.Position.mode == mode, models.Position.status == "open",
                               models.Position.quantity > 0)).all()
        orders = s.execute(select(models.Order, models.TradingPair.symbol)
                           .join(models.TradingPair, models.TradingPair.id == models.Order.pair_id)
                           .where(models.Order.mode == mode, models.Order.status.in_(
                               ("pending_submit", "submitted", "open", "partially_filled",
                                "unknown")))).all()
        out["modes"][mode] = {
            "open_positions": [{"id": p.id, "symbol": sym, "quantity": ser(p.quantity),
                                "entry_price": ser(p.avg_entry_price),
                                "stop_loss": ser(p.stop_loss)} for p, sym in pos],
            "active_orders": [{"id": o.id, "symbol": sym, "side": o.side, "purpose": o.purpose,
                               "status": o.status, "quantity": ser(o.amount)}
                              for o, sym in orders],
            "close_all_requested": bool(await redis.get(close_all_key(mode))),
            "confirm_phrase": CLOSE_CONFIRM[mode],
        }
    return out


@router.post("/risk/kill-switch/close-positions", dependencies=[Depends(owner)])
async def kill_close_positions(body: ClosePositionsBody, s: Session = Depends(db),
                               redis: Any = Depends(redis_dep),
                               viewer: Viewer = Depends(current_viewer),
                               settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Ask the trader to close every open position of a mode with bounded-slippage IOC
    sells. Only while the kill switch is engaged; typed confirmation; live also needs 2FA."""
    from nexus_execution.trader import close_all_key

    if await _kill_status(s, redis) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "engage the kill switch first")
    if body.confirm != CLOSE_CONFIRM[body.mode]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"type exactly: {CLOSE_CONFIRM[body.mode]}")
    if body.mode == "live":
        await check_totp(viewer, body.totp, settings, redis)
    await redis.set(close_all_key(body.mode), viewer.actor, ex=6 * 3600)
    repo.record_audit(s, actor=viewer.actor, action="kill_switch_close_requested",
                      user_id=viewer.user.id, details={"mode": body.mode})
    return {"requested": True, "mode": body.mode,
            "note": "The trader places the sells on its next cycle (about 30 s)."}


class KillBody(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    totp: str | None = Field(default=None, max_length=6)


@router.post("/risk/kill-switch", dependencies=[Depends(owner)])
async def engage_kill(body: KillBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                      viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    # Engaging is never gated behind 2FA: stopping must always be easy.
    await KillSwitch(redis).engage(body.reason, actor=viewer.actor)
    repo.record_audit(s, actor=viewer.actor, action="kill_switch_engaged",
                      user_id=viewer.user.id, details={"reason": body.reason})
    for mode in ("paper", "live"):
        repo.record_risk_event(s, mode=mode, event_type="kill_switch", severity="critical",
                               details={"reason": body.reason, "actor": viewer.actor})
    return {"engaged": True}


@router.post("/risk/kill-switch/clear", dependencies=[Depends(owner)])
async def clear_kill(body: KillBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                     viewer: Viewer = Depends(current_viewer),
                     settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    await check_totp(viewer, body.totp, settings, redis)
    await KillSwitch(redis).clear(actor=viewer.actor, reason=body.reason)
    repo.record_audit(s, actor=viewer.actor, action="kill_switch_cleared",
                      user_id=viewer.user.id, details={"reason": body.reason})
    return {"engaged": False}


# ------------------------------------------------------------------ system / security
@router.get("/system")
async def system(s: Session = Depends(db), redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    beats = {}
    for name in ("market-data:candles", "market-data:health", "market-data:books",
                 "trader:paper", "trader:live", "notifier"):
        raw = await redis.get(events.heartbeat_key(name))
        beats[name] = raw.decode() if isinstance(raw, bytes) else raw
    ev = s.scalars(select(models.SystemEvent).order_by(desc(models.SystemEvent.id)).limit(100)).all()
    counts = {t: s.scalar(select(func.count()).select_from(getattr(models, m))) for t, m in (
        ("candles", "MarketCandle"), ("orders", "Order"), ("signals", "TradingSignal"))}
    return {"heartbeats": beats, "events": _rows(ev, ["id", "component", "event_type", "severity",
                                                      "message", "created_at"]),
            "counts": counts, "server_time": ser(utcnow())}


@router.get("/security", dependencies=[Depends(owner)])
async def security(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                   settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    audits = s.scalars(select(models.AuditLog).order_by(desc(models.AuditLog.id)).limit(100)).all()
    live = await live_gate_problems(settings, s, redis,
                                    [e.value for e in settings.enabled_exchanges])
    return {"live_trading_blockers": live, "live_trading_permitted": not live,
            "audit": _rows(audits, ["id", "actor", "action", "target_type", "target_id",
                                    "details", "ip_address", "created_at"])}


Market = Literal["spot", "futures"]


class CredentialBody(BaseModel):
    exchange: Literal["binance", "kraken", "coinbase"]
    label: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=8, max_length=4096)
    api_secret: str = Field(min_length=8, max_length=8192)
    withdrawals_disabled_confirmed: bool
    totp: str = Field(min_length=6, max_length=6)
    environment: Literal["testnet", "demo", "production"] = "testnet"
    markets: list[Market] = Field(default_factory=lambda: list[Market](["futures"]),
                                  min_length=1, max_length=2)


@router.post("/security/credentials", dependencies=[Depends(owner)])
async def add_credential(body: CredentialBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                         viewer: Viewer = Depends(current_viewer),
                         settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    await check_totp(viewer, body.totp, settings, redis)
    if not body.withdrawals_disabled_confirmed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "create the key with withdrawals DISABLED and confirm it")
    if settings.credential_encryption_keys is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "encryption not configured")
    ring = KeyRing.parse(settings.credential_encryption_keys.get_secret_value())
    cipher = CredentialCipher(ring)
    cred = models.ExchangeCredential(
        user_id=viewer.user.id, exchange_id=repo.exchange_id(s, body.exchange), label=body.label,
        environment={"testnet": "sandbox", "demo": "demo"}.get(body.environment, "production"),
        api_key_encrypted=b"pending", api_secret_encrypted=b"pending",
        key_fingerprint=key_fingerprint(body.api_key), encryption_key_id=ring.primary_id,
        # Owner attestation; exchanges with an API check are re-verified in Phase 3 tooling.
        withdrawal_disabled_verified=True,
        permissions={"owner_confirmed_no_withdrawals": True, "markets": sorted(set(body.markets))})
    s.add(cred)
    try:
        s.flush()
    except Exception as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "an active credential already exists for this exchange") from exc
    ctx = {"user_id": str(viewer.user.id), "exchange": body.exchange, "credential_id": str(cred.id)}
    cred.api_key_encrypted = cipher.encrypt(body.api_key, context={**ctx, "field": "api_key"})
    cred.api_secret_encrypted = cipher.encrypt(body.api_secret,
                                               context={**ctx, "field": "api_secret"})
    repo.record_audit(s, actor=viewer.actor, action="credential_added", user_id=viewer.user.id,
                      target_type="exchange_credential", target_id=str(cred.id),
                      details={"exchange": body.exchange, "fingerprint": cred.key_fingerprint,
                               "environment": body.environment, "markets": sorted(set(body.markets))})
    return {"id": cred.id, "fingerprint": cred.key_fingerprint}


def _cred_view(s: Session, c: models.ExchangeCredential) -> dict[str, Any]:
    ex = s.get(models.Exchange, c.exchange_id)
    perms = c.permissions or {}
    return {"id": c.id, "exchange": ex.code if ex else None, "label": c.label,
            "environment": {"sandbox": "testnet", "demo": "demo"}.get(c.environment, "production"),
            "markets": perms.get("markets", []), "fingerprint": c.key_fingerprint,
            "created_at": ser(c.created_at), "last_verified_at": ser(c.last_verified_at),
            "last_check": perms.get("last_check")}


@router.get("/security/credentials", dependencies=[Depends(owner)])
def list_credentials(s: Session = Depends(db),
                     viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """Connection metadata only: keys and secrets are never returned."""
    rows = s.scalars(select(models.ExchangeCredential).where(
        models.ExchangeCredential.user_id == viewer.user.id,
        models.ExchangeCredential.revoked_at.is_(None)).order_by(models.ExchangeCredential.id)).all()
    return {"credentials": [_cred_view(s, c) for c in rows]}


@router.post("/security/credentials/{cred_id}/test", dependencies=[Depends(owner)])
async def test_credential(cred_id: int, s: Session = Depends(db),
                          redis: Any = Depends(redis_dep),
                          viewer: Viewer = Depends(current_viewer),
                          settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Real signed, read-only Binance calls with the stored key. Places no orders."""
    from nexus_api import connection

    cred = s.get(models.ExchangeCredential, cred_id)
    if cred is None or cred.user_id != viewer.user.id or cred.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    ex = s.get(models.Exchange, cred.exchange_id)
    if ex is None or ex.code != "binance":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "connection test supports Binance only")
    if settings.credential_encryption_keys is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "encryption not configured")
    cipher = CredentialCipher(KeyRing.parse(settings.credential_encryption_keys.get_secret_value()))
    ctx = {"user_id": str(cred.user_id), "exchange": ex.code, "credential_id": str(cred.id)}
    api_key = cipher.decrypt(cred.api_key_encrypted, context={**ctx, "field": "api_key"})
    secret = cipher.decrypt(cred.api_secret_encrypted, context={**ctx, "field": "api_secret"})
    markets = list((cred.permissions or {}).get("markets") or ["futures"])
    env_name = connection.ENV_NAMES.get(cred.environment, cred.environment)
    banned = await binance_ban.active_ban(redis, env_name)
    if banned is not None:
        # No call during a ban (it would extend it); the stored last check is kept.
        msg = binance_ban.ban_message(banned)
        return {"credential": _cred_view(s, cred),
                "result": {"ok": False, "environment": env_name, "checked_at":
                           utcnow().isoformat(), **{m: {"ok": False, "error": msg}
                                                     for m in markets}}}
    result = await connection.probe(api_key, secret, environment=cred.environment,
                                    markets=markets, cafile=settings.extra_ca_file)
    for m in markets:
        until = await binance_ban.record_ban(redis, env_name, str(result[m].get("error") or ""))
        if until is not None:
            result[m]["error"] = binance_ban.ban_message(until)
    cred.permissions = {**(cred.permissions or {}), "last_check": result}
    if result["ok"]:
        cred.last_verified_at = utcnow()
    repo.record_audit(s, actor=viewer.actor, action="credential_tested", user_id=viewer.user.id,
                      target_type="exchange_credential", target_id=str(cred.id),
                      details={"ok": result["ok"], "environment": result["environment"]})
    return {"credential": _cred_view(s, cred), "result": result}


@router.delete("/security/credentials/{cred_id}", dependencies=[Depends(owner)])
def revoke_credential(cred_id: int, s: Session = Depends(db),
                      viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    cred = s.get(models.ExchangeCredential, cred_id)
    if cred is None or cred.user_id != viewer.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    cred.revoked_at = utcnow()
    repo.record_audit(s, actor=viewer.actor, action="credential_revoked", user_id=viewer.user.id,
                      target_type="exchange_credential", target_id=str(cred_id))
    return {"revoked": True}


class ApprovalBody(BaseModel):
    approve: bool
    acknowledgement: str
    totp: str = Field(min_length=6, max_length=6)


@router.post("/security/live-approval", dependencies=[Depends(owner)])
async def live_approval(body: ApprovalBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                        viewer: Viewer = Depends(current_viewer),
                        settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    from nexus_config import LIVE_TRADING_ACK_PHRASE
    await check_totp(viewer, body.totp, settings, redis)
    if body.approve and body.acknowledgement != LIVE_TRADING_ACK_PHRASE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "type the acknowledgement exactly")
    repo.record_audit(s, actor=viewer.actor, user_id=viewer.user.id,
                      action="live_trading_approved" if body.approve else "live_trading_revoked")
    return {"approved": body.approve}



class BotAction(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.get("/bot")
async def bot_status(redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    from nexus_common.botstate import KEY, get_state

    state = await get_state(redis)
    raw = await redis.get(KEY)
    details = json.loads(raw) if raw else None
    return {"state": state.value, "details": details}


@router.post("/bot/{action}", dependencies=[Depends(owner)])
async def bot_action(action: Literal["start", "pause", "stop"], body: BotAction,
                     s: Session = Depends(db), redis: Any = Depends(redis_dep),
                     viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """START resumes new entries; PAUSE and STOP block them. Stopping never closes positions."""
    from nexus_common.botstate import BotState, set_state

    target = {"start": BotState.RUNNING, "pause": BotState.PAUSED, "stop": BotState.STOPPED}[action]
    await set_state(redis, target, actor=viewer.actor, reason=body.reason)
    repo.record_audit(s, actor=viewer.actor, action=f"bot_{action}", user_id=viewer.user.id,
                      target_type="bot", details={"reason": body.reason})
    return {"state": target.value}


@router.get("/account/{market}")
async def live_account(market: Literal["spot", "futures"], s: Session = Depends(db),
                       redis: Any = Depends(redis_dep), viewer: Viewer = Depends(current_viewer),
                       settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Live Binance account data for the dashboard, read with the operator's stored key."""
    from nexus_api.account import account_view

    owner_id = viewer.user.id
    if viewer.user.role != "owner":
        owner = s.scalar(select(models.User).where(models.User.role == "owner"))
        if owner is None:
            return {"connected": False, "market": market, "message": "No owner account."}
        owner_id = owner.id
    return await account_view(market, s, owner_id, settings, redis)


TELEGRAM_EVENTS = [
    ("signal", "New trading opportunity (signal accepted or rejected)"),
    ("order_submitted", "Trade opened (order submitted and confirmed)"),
    ("take_profit", "Take-profit reached"), ("stop_loss", "Stop-loss triggered"),
    ("risk_warning", "Risk limit warning or limit reached"),
    ("exchange_disconnected", "Exchange disconnected"), ("exchange_connected", "Exchange reconnected"),
    ("emergency_shutdown", "Emergency shutdown / kill switch"),
    ("order_state_unknown", "Order state unknown (needs reconciliation)"),
    ("system_alert", "System alerts (bot started, stopped, services down)"),
    ("daily_report", "Daily trading report"),
]


@router.get("/telegram", dependencies=[Depends(owner)])
def telegram_status(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Configuration status only: the bot token is never returned."""
    chat = settings.telegram_chat_id or ""
    return {"configured": bool(settings.telegram_bot_token and chat),
            "chat_id": (chat[:3] + "…" + chat[-2:]) if len(chat) > 6 else (chat and "set"),
            "events": [{"type": t, "description": d} for t, d in TELEGRAM_EVENTS]}


@router.post("/telegram/test", dependencies=[Depends(owner)])
async def telegram_test(s: Session = Depends(db), viewer: Viewer = Depends(current_viewer),
                        settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Send one real test message through the Telegram Bot API."""
    from nexus_notify.telegram import TelegramClient

    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Telegram is not configured: set NEXUS_TELEGRAM_BOT_TOKEN and "
                            "NEXUS_TELEGRAM_CHAT_ID on the server")
    client = TelegramClient(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id)
    try:
        result = await client.send("✅ <b>NEXUS AI</b>: test message from the dashboard.")
    finally:
        await client.close()
    repo.record_audit(s, actor=viewer.actor, action="telegram_test", user_id=viewer.user.id,
                      details={"ok": result.ok})
    return {"ok": result.ok, "error": result.error}


@router.post("/risk/streak/reset", dependencies=[Depends(owner)])
async def reset_streak(body: KillBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                       viewer: Viewer = Depends(current_viewer),
                       settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Resume after a loss-streak pause. Like clearing the kill switch, this needs 2FA."""
    from nexus_execution.protection import STREAK_RESET_KEY

    await check_totp(viewer, body.totp, settings, redis)
    now = utcnow()
    await redis.set(STREAK_RESET_KEY, now.isoformat())
    repo.record_audit(s, actor=viewer.actor, action="loss_streak_reset", user_id=viewer.user.id,
                      details={"reason": body.reason})
    return {"reset_at": ser(now)}


@router.get("/backtests")
def backtests(market: Literal["all", "spot", "futures"] = "all",
              s: Session = Depends(db)) -> dict[str, Any]:
    """Recorded walk-forward / backtest runs. Out-of-sample results only; never live P&L."""
    rows = s.execute(select(models.BacktestRun, models.Strategy.code).outerjoin(models.Strategy)
                     .order_by(desc(models.BacktestRun.id)).limit(200)).all()
    out = []
    for r, code in rows:
        cfg = r.config or {}
        mkt = cfg.get("market", "spot")
        if market != "all" and mkt != market:
            continue
        m = r.metrics or {}
        out.append({"id": r.id, "strategy": code or cfg.get("strategy"), "market": mkt,
                    "walk_forward": r.is_walk_forward,
                    "exchange": cfg.get("exchange"), "symbols": cfg.get("symbols"),
                    "side": cfg.get("side", "long-only"), "funding": cfg.get("funding"),
                    "train_days": cfg.get("train_days"), "test_days": cfg.get("test_days"),
                    "data_start": ser(r.data_start), "data_end": ser(r.data_end),
                    "fee_bps": ser(r.fee_bps), "slippage_bps": ser(r.slippage_bps),
                    "summary": m.get("summary"), "passed": m.get("passed"),
                    "failures": m.get("failures"), "windows": m.get("windows"),
                    "oos_funding_paid": m.get("oos_funding_paid"),
                    "completed_at": ser(r.completed_at)})
    return {"runs": out}


@router.get("/intelligence")
async def intelligence(redis: Any = Depends(redis_dep),
                       settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Live analysis of the configured pairs from Binance public market data (no orders)."""
    return await _intelligence(redis, settings)


async def _news_risk(redis: Any) -> float | None:
    from nexus_news.service import KEY as NEWS_KEY

    raw = await redis.get(NEWS_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw).get("score")
        return None if value is None else float(value)
    except (ValueError, AttributeError):
        return None


async def _intelligence(redis: Any, settings: Settings) -> dict[str, Any]:
    from nexus_api.intelligence import market_intelligence

    base = settings.binance_public_api_url or "https://api.binance.com/api/v3"
    order_quote = settings.paper_initial_capital * settings.risk.max_asset_concentration
    return await market_intelligence(list(settings.trading_pairs), base, redis=redis,
                                     news_risk=await _news_risk(redis),
                                     kill_switch=await KillSwitch(redis).is_engaged(),
                                     order_quote=order_quote)


@router.get("/agents/proposals")
async def agent_proposals(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                          settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """One explained proposal per trading pair: six agents, then the independent Risk
    Manager. Advisory only: nothing here places orders."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_agents import Context, build_proposal
    from nexus_social.service import RISK as X_RISK
    from nexus_social.service import SNAPSHOT as X_SNAPSHOT

    intel = await _intelligence(redis, settings)
    markets = {str(r.get("symbol")): r for r in intel.get("pairs") or []}
    strategies = [{"code": st.code, "enabled": st.is_enabled,
                   "validation": (st.parameters or {}).get("validation") or {}}
                  for st in s.scalars(select(models.Strategy).order_by(models.Strategy.id))]
    p = _portfolio(s, "paper")
    snap = None if p is None else s.scalar(select(models.PerformanceSnapshot).where(
        models.PerformanceSnapshot.portfolio_id == p.id)
        .order_by(desc(models.PerformanceSnapshot.captured_at)).limit(1))
    held = [] if p is None else list(s.scalars(select(models.Position.base_asset).where(
        models.Position.portfolio_id == p.id, models.Position.status == "open",
        models.Position.quantity > 0)))
    equity = snap.equity if snap else settings.paper_initial_capital
    portfolio = {"equity": str(equity), "cash": str(snap.cash if snap else equity),
                 "exposure": str(snap.exposure if snap else 0), "open_positions": len(held),
                 "held_assets": held, "source": "latest paper snapshot" if snap else
                 "initial paper capital (no snapshot yet)"}
    raw_x = await redis.get(X_SNAPSHOT)
    xsnap = json.loads(raw_x) if raw_x else {}
    raw_blocks = await redis.get(X_RISK)
    blocks = (json.loads(raw_blocks).get("blocks") or {}) if raw_blocks else {}
    kill = await _kill_status(s, redis) is not None
    news = await _news_risk(redis)
    now = _dt.now(UTC)
    out = []
    for pair in settings.trading_pairs:
        coin = pair.split("/")[0]
        ctx = Context(
            symbol=pair, market=markets.get(pair), strategies=strategies, portfolio=portfolio,
            risk=settings.risk, news_risk=news, news_threshold=settings.news_block_threshold,
            kill_switch=kill, now=now,
            x_coin=next((c for c in xsnap.get("coins") or [] if c.get("coin") == coin), None),
            x_alerts=[a for a in xsnap.get("alerts") or [] if a.get("coin") in (coin, "MARKET")],
            x_block=blocks.get(coin) or blocks.get("MARKET"),
            fee_rate=settings.paper_fee_bps / 10_000,
            slippage_rate=settings.paper_slippage_bps / 10_000,
            futures_enabled=settings.futures_enabled)
        out.append(build_proposal(ctx))
    return {"generated_at": now.isoformat(), "mode": "paper", "portfolio": portfolio,
            "proposals": out,
            "disclaimer": "Advisory analysis only. No guaranteed profits; 'no trade' is a "
                          "valid decision. Orders are placed only by the trader after the "
                          "same gates pass again."}


@router.get("/execution/quality")
def execution_quality_view(mode: Mode = "paper", s: Session = Depends(db),
                           settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    from nexus_execution.reports import execution_quality

    p = _portfolio(s, mode)
    sim = None
    if mode == "paper":
        sim = {"latency_ms": settings.paper_latency_ms,
               "book_participation": str(settings.paper_book_participation),
               "max_book_age_seconds": settings.paper_max_book_age_seconds,
               "outage_rate": settings.paper_outage_rate,
               "fee_bps": str(settings.paper_fee_bps),
               "extra_slippage_bps": str(settings.paper_slippage_bps)}
    return {"mode": mode, "simulation": sim,
            "quality": execution_quality(s, p.id) if p else None}


@router.get("/agents/reviews")
def agent_reviews(mode: Mode = "paper", s: Session = Depends(db),
                  settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Process-based review of the last 50 closed trades."""
    from nexus_agents import review_trade

    p = _portfolio(s, mode)
    if p is None:
        return {"mode": mode, "reviews": [], "summary": {}}
    rows = s.execute(select(models.Position, models.TradingPair.symbol)
                     .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
                     .where(models.Position.portfolio_id == p.id,
                            models.Position.status == "closed")
                     .order_by(desc(models.Position.closed_at)).limit(50)).all()

    def order_dict(o: models.Order) -> dict[str, Any]:
        return {"expected_price": ser(o.expected_price),
                "average_fill_price": ser(o.average_fill_price),
                "filled_amount": ser(o.filled_amount), "details": o.details or {}}

    reviews = []
    for pos, sym in rows:
        orders = s.scalars(select(models.Order).where(models.Order.position_id == pos.id)
                           .order_by(models.Order.id)).all()
        entry = next((o for o in orders if o.purpose == "entry"), None)
        if entry is None and pos.entry_order_id:
            entry = s.get(models.Order, pos.entry_order_id)
        reviews.append(review_trade(
            {"id": pos.id, "symbol": sym, "realized_pnl": ser(pos.realized_pnl),
             "exit_reason": pos.exit_reason, "closed_at": ser(pos.closed_at)},
            order_dict(entry) if entry else None,
            [order_dict(o) for o in orders if o.purpose == "exit"],
            slippage_bps_assumed=settings.paper_slippage_bps))
    summary: dict[str, int] = {}
    for r in reviews:
        for lab in r["labels"]:
            summary[lab] = summary.get(lab, 0) + 1
    return {"mode": mode, "reviews": reviews, "summary": summary}


@router.get("/x/status")
async def x_status(redis: Any = Depends(redis_dep),
                   settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """X (Twitter) integration status: configuration, last refresh, spend and limits."""
    from nexus_social.service import SNAPSHOT, STATUS

    raw_status = await redis.get(STATUS)
    raw_snap = await redis.get(SNAPSHOT)
    snap = json.loads(raw_snap) if raw_snap else None
    return {
        "configured": settings.x_bearer_token is not None,
        "read_only": True,
        "coins": [c.upper() for c in settings.x_coins],
        "watch_accounts": list(settings.x_watch_accounts),
        "refresh_minutes": settings.x_refresh_minutes,
        "max_posts_per_query": settings.x_max_posts_per_query,
        "daily_budget_usd": str(settings.x_daily_budget_usd),
        "status": json.loads(raw_status) if raw_status else None,
        "usage": (snap or {}).get("usage"),
        "snapshot_at": (snap or {}).get("generated_at"),
    }


@router.get("/x/sentiment")
async def x_sentiment(redis: Any = Depends(redis_dep),
                      settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Latest X sentiment per coin with news alerts, trends, history and market context.
    Context only: X data never opens a trade."""
    from nexus_social.analysis import POLICY, combine
    from nexus_social.service import HISTORY, SNAPSHOT

    raw = await redis.get(SNAPSHOT)
    if raw is None:
        return {"available": False, "configured": settings.x_bearer_token is not None,
                "policy": POLICY,
                "message": ("No X data yet. " + ("The first refresh has not finished."
                            if settings.x_bearer_token is not None else
                            "Set NEXUS_X_BEARER_TOKEN on the server to enable X sentiment."))}
    snap = json.loads(raw)
    market: dict[str, Any] = {}
    cached = await redis.get("nexus:intelligence")
    if cached:
        for row in json.loads(cached).get("pairs") or []:
            market[str(row.get("symbol", "")).split("/")[0]] = row
    traded = {p.split("/")[0].upper() for p in settings.trading_pairs}
    for c in snap.get("coins") or []:
        if c["coin"] in traded and c["coin"] not in market:
            c["market"] = {"available": False, "note": "market context not loaded: open the "
                           "AI intelligence page (Binance analysis is cached for 30 s)"}
        else:
            c["market"] = combine(c, market.get(c["coin"]))
    history = [json.loads(x) for x in await redis.lrange(HISTORY, -84, -1)]
    return {"available": True, **snap, "history": history,
            "market_context_note": None if cached else
            "Open the Intelligence page to load market context (cached 30 s)."}


@router.get("/news")
async def news(redis: Any = Depends(redis_dep),
               settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Latest public news items with the keyword impact used for the news-risk gate."""
    from nexus_news.service import KEY as NEWS_KEY

    raw = await redis.get(NEWS_KEY)
    data = json.loads(raw) if raw else None
    return {"available": data is not None, "threshold": settings.news_block_threshold,
            "blocks_entries": data is None or data.get("score") is None
            or float(data["score"]) > settings.news_block_threshold,
            **(data or {"message": "news monitor has not reported (entries are blocked "
                                   "until it does)"})}


@router.get("/settings/summary")
def settings_summary(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Effective configuration (no secrets). Changes are made on the server, not here."""
    r = settings.risk
    return {"config": settings.safe_summary(),
            "risk_extra": {"max_correlated_exposure": str(r.max_correlated_exposure),
                           "correlation_threshold": r.correlation_threshold,
                           "cooldown_after_stop_minutes": r.cooldown_after_stop_minutes,
                           "max_asset_concentration": str(r.max_asset_concentration)},
            "paper_simulation": {"latency_ms": settings.paper_latency_ms,
                                 "book_participation": str(settings.paper_book_participation),
                                 "max_book_age_seconds": settings.paper_max_book_age_seconds,
                                 "outage_rate": settings.paper_outage_rate,
                                 "fee_bps": str(settings.paper_fee_bps),
                                 "slippage_bps": str(settings.paper_slippage_bps),
                                 "initial_capital": str(settings.paper_initial_capital)},
            "x": {"configured": settings.x_bearer_token is not None,
                  "coins": list(settings.x_coins), "refresh_minutes": settings.x_refresh_minutes,
                  "daily_budget_usd": str(settings.x_daily_budget_usd)},
            "news_block_threshold": settings.news_block_threshold,
            "live_trading_blockers": settings.live_trading_config_problems(),
            "how_to_change": "Server environment variables (Render: nexus-ai-fra -> "
                             "Environment). Absolute risk ceilings cannot be exceeded."}


@router.get("/overview/context")
async def overview_context(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                           settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Market regime per pair, X sentiment, news and risk status for the Overview."""
    from nexus_common.botstate import get_state
    from nexus_social.service import RISK as X_RISK
    from nexus_social.service import SNAPSHOT as X_SNAPSHOT

    intel = await redis.get("nexus:intelligence")
    pairs = json.loads(intel).get("pairs", []) if intel else []
    xs = await redis.get(X_SNAPSHOT)
    xb = await redis.get(X_RISK)
    news_score = await _news_risk(redis)
    blocks = (json.loads(xb).get("blocks") or {}) if xb else {}
    return {
        "regimes": [{"symbol": p.get("symbol"), "regime": p.get("regime"),
                     "direction": p.get("direction")} for p in pairs if not p.get("error")],
        "regimes_note": None if intel else "open AI analysis to refresh market regimes",
        "sentiment": [{"coin": c["coin"], "score": c["score"], "label": c["label"],
                       "confidence": c["confidence"]}
                      for c in (json.loads(xs).get("coins") or [])] if xs else [],
        "risk_status": {
            "kill_switch": await _kill_status(s, redis) is not None,
            "bot_state": (await get_state(redis)).value,
            "news_risk": news_score, "news_threshold": settings.news_block_threshold,
            "news_blocks_entries": news_score is None
            or news_score > settings.news_block_threshold,
            "x_blocks": blocks,
            "live_trading_blockers": len(settings.live_trading_config_problems())},
    }


BINANCE_PINGS = {
    "spot (live)": "https://api.binance.com/api/v3/ping",
    "futures (live)": "https://fapi.binance.com/fapi/v1/ping",
    "spot (demo)": "https://demo-api.binance.com/api/v3/ping",
    "futures (demo)": "https://demo-fapi.binance.com/fapi/v1/ping",
    "futures (testnet)": "https://testnet.binancefuture.com/fapi/v1/ping",
    "spot public data (mirror)": "https://data-api.binance.vision/api/v3/ping",
}


@router.get("/system/binance-reachability", dependencies=[Depends(owner)])
async def binance_reachability() -> dict[str, Any]:
    """Unauthenticated pings from THIS server. HTTP 451 means Binance refuses the server's
    location; NEXUS reports it and never works around it."""
    import httpx

    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=10, trust_env=True) as client:
        for name, url in BINANCE_PINGS.items():
            try:
                r = await client.get(url)
                out[name] = {"status": r.status_code, "reachable": r.status_code == 200,
                             "blocked_by_location": r.status_code == 451}
            except httpx.HTTPError as exc:
                out[name] = {"status": None, "reachable": False, "error": type(exc).__name__}
    return {"checked_at": ser(utcnow()), "hosts": out}


@router.post("/security/credentials/{cred_id}/order-test", dependencies=[Depends(owner)])
async def credential_order_test(cred_id: int, market: Literal["futures", "spot"] = "futures",
                                s: Session = Depends(db),
                                redis: Any = Depends(redis_dep),
                                viewer: Viewer = Depends(current_viewer),
                                settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Real order round trip on a DEMO or TESTNET futures key (never production)."""
    from nexus_exchange.binance_futures import BinanceFuturesAdapter
    from nexus_exchange.binance_futures_transport import CcxtFuturesTransport

    from nexus_api.account import decrypt
    from nexus_api.connection import ENV_NAMES, explain
    from nexus_api.ordertest import run_round_trip

    cred = s.get(models.ExchangeCredential, cred_id)
    if cred is None or cred.user_id != viewer.user.id or cred.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    env = ENV_NAMES.get(cred.environment, cred.environment)
    if env not in ("demo", "testnet"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "the order test runs only on demo or testnet keys, never production")
    if market not in ((cred.permissions or {}).get("markets") or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"this key has no {market} access")
    banned = await binance_ban.active_ban(redis, env)
    if banned is not None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, binance_ban.ban_message(banned))
    if not await redis.set(f"nexus:order_test:{cred_id}", "1", nx=True, ex=120):
        raise HTTPException(status.HTTP_409_CONFLICT, "an order test is already running")
    api_key, secret = decrypt(cred, settings)
    if market == "spot":
        import ccxt.async_support as ccxt_async

        from nexus_api.ordertest import run_spot_round_trip

        config: dict[str, Any] = {"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                                  "timeout": 10_000, "aiohttp_trust_env": True,
                                  "options": {"adjustForTimeDifference": True}}
        if settings.extra_ca_file:
            config["cafile"] = settings.extra_ca_file
        client = ccxt_async.binance(config)
        if env == "demo":
            client.enable_demo_trading(True)
        else:
            client.set_sandbox_mode(True)
        try:
            from nexus_exchange.factory import load_binance_spot_markets

            await load_binance_spot_markets(client, ["BTC/USDT"])
            result = await run_spot_round_trip(client)
        except Exception as exc:
            result = {"ok": False, "steps": [], "alerts": [], "error": explain(exc)}
        finally:
            await client.close()
            await redis.delete(f"nexus:order_test:{cred_id}")
    else:
        transport = CcxtFuturesTransport(api_key, secret, cafile=settings.extra_ca_file,
                                         environment=env)
        try:
            result = await run_round_trip(BinanceFuturesAdapter(transport))
        except Exception as exc:
            result = {"ok": False, "steps": [], "alerts": [], "error": explain(exc)}
        finally:
            await transport.close()
            await redis.delete(f"nexus:order_test:{cred_id}")
    result["environment"] = env
    result["market"] = market
    repo.record_audit(s, actor=viewer.actor, action="order_test", user_id=viewer.user.id,
                      target_type="exchange_credential", target_id=str(cred_id),
                      details={"ok": result["ok"], "environment": env, "market": market,
                               "steps": [x["step"] + (" ok" if x["ok"] else " FAILED")
                                         for x in result["steps"]]})
    return result
