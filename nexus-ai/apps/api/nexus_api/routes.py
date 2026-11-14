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
        "kill_switch": await KillSwitch(redis).status(),
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


@router.get("/markets/candles")
def candles(exchange: str, symbol: str, timeframe: Literal["5m", "15m", "1h", "4h"] = "1h",
            limit: int = Query(300, ge=10, le=2000), s: Session = Depends(db)) -> dict[str, Any]:
    try:
        pid = repo.pair_id(s, exchange, symbol)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    rows = repo.load_candles(s, pid, timeframe, limit=limit)
    return {"exchange": exchange, "symbol": symbol, "timeframe": timeframe,
            "candles": [{"t": int(c.open_time.timestamp()), "o": str(c.open), "h": str(c.high),
                         "l": str(c.low), "c": str(c.close), "v": str(c.volume)} for c in rows]}


@router.get("/markets/books")
async def books(redis: Any = Depends(redis_dep),
                settings: Settings = Depends(settings_dep)) -> list[dict[str, Any]]:
    out = []
    for ex in settings.enabled_exchanges:
        for sym in settings.trading_pairs:
            raw = await redis.get(events.book_key(ex.value, sym))
            data = json.loads(raw) if raw else None
            out.append({"exchange": ex.value, "symbol": sym, "book": data})
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
    return {"mode": mode, "limits": {k: str(v) for k, v in settings.risk.model_dump().items()},
            "kill_switch": await KillSwitch(redis).status(),
            "current": None if last is None else {
                "equity": ser(last.equity), "peak_equity": ser(last.peak_equity),
                "drawdown": ser(last.drawdown), "exposure": ser(last.exposure),
                "exposure_pct": ser(last.exposure / last.equity) if last.equity else None,
                "open_positions": last.open_positions, "as_of": ser(last.captured_at)},
            "events": _rows(ev, ["id", "event_type", "severity", "details", "created_at"])}


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


class CredentialBody(BaseModel):
    exchange: Literal["binance", "kraken", "coinbase"]
    label: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=8, max_length=4096)
    api_secret: str = Field(min_length=8, max_length=8192)
    withdrawals_disabled_confirmed: bool
    totp: str = Field(min_length=6, max_length=6)


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
        api_key_encrypted=b"pending", api_secret_encrypted=b"pending",
        key_fingerprint=key_fingerprint(body.api_key), encryption_key_id=ring.primary_id,
        # Owner attestation; exchanges with an API check are re-verified in Phase 3 tooling.
        withdrawal_disabled_verified=True, permissions={"owner_confirmed_no_withdrawals": True})
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
                      details={"exchange": body.exchange, "fingerprint": cred.key_fingerprint})
    return {"id": cred.id, "fingerprint": cred.key_fingerprint}


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

