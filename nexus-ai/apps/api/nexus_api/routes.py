"""Dashboard API. Every route requires an authenticated session; mutations also pass the
CSRF guard, are owner-only, are audit-logged, and sensitive ones require TOTP.

Monetary values are returned as exact decimal strings. Missing data is ``null``.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
TESTNET_ACTION = "testnet_round_trip"


class TestnetRun(BaseModel):
    totp: str | None = Field(default=None, max_length=6)
    symbol: Literal["BTC/USDT", "ETH/USDT"] = "BTC/USDT"


@router.post("/system/testnet/round-trip", dependencies=[Depends(owner)])
async def testnet_round_trip(body: TestnetRun, s: Session = Depends(db),
                             redis: Any = Depends(redis_dep),
                             viewer: Viewer = Depends(current_viewer),
                             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Binance Spot TESTNET round trip with the owner's stored TESTNET key (test funds only):
    connectivity, balances, market data, signal, risk sizing, a buy and a sell, fill
    reconciliation and a restart drill. The evidence goes to the audit log; never the key."""
    from nexus_config import ExchangeCode
    from nexus_exchange.factory import build_adapter
    from nexus_execution.testnet_run import round_trip

    from nexus_api.account import decrypt

    await check_totp(viewer, body.totp or "", settings, redis)
    cred = s.scalar(select(models.ExchangeCredential).join(
        models.Exchange, models.Exchange.id == models.ExchangeCredential.exchange_id).where(
        models.ExchangeCredential.user_id == viewer.user.id,
        models.ExchangeCredential.environment == "sandbox",
        models.ExchangeCredential.revoked_at.is_(None), models.Exchange.code == "binance")
        .order_by(desc(models.ExchangeCredential.id)))
    if cred is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "add a Binance Spot TESTNET key first "
                            "(Security > API keys, environment: testnet)")
    key, secret = decrypt(cred, settings)
    adapter = build_adapter(ExchangeCode.BINANCE, api_key=key, secret=secret, sandbox=True,
                            ca_file=settings.extra_ca_file,
                            keep_symbols=frozenset({body.symbol}))
    try:
        evidence = await round_trip(adapter, symbol=body.symbol)
    finally:
        await adapter.close()
    repo.record_audit(s, actor=viewer.actor, user_id=viewer.user.id, action=TESTNET_ACTION,
                      target_type="exchange_credential", target_id=str(cred.id),
                      details=evidence)
    return evidence


@router.get("/system/testnet/round-trip")
async def testnet_round_trip_history(s: Session = Depends(db)) -> dict[str, Any]:
    rows = s.scalars(select(models.AuditLog).where(models.AuditLog.action == TESTNET_ACTION)
                     .order_by(desc(models.AuditLog.id)).limit(10)).all()
    return {"runs": [{"at": ser(r.created_at), **(r.details or {})} for r in rows]}


CAPS_KEY = "nexus:exchanges:capabilities"


@router.get("/exchanges/capabilities")
async def exchange_capabilities(redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Last live capability detection per exchange (public data via CCXT, no keys)."""
    raw = await redis.get(CAPS_KEY)
    return json.loads(raw) if raw else {"checked_at": None, "exchanges": {}}


@router.post("/exchanges/capabilities/refresh", dependencies=[Depends(owner)])
async def refresh_exchange_capabilities(redis: Any = Depends(redis_dep),
                                        settings: Settings = Depends(settings_dep)
                                        ) -> dict[str, Any]:
    """Re-detect Binance, OKX, Bybit and Kraken from their PUBLIC APIs (read-only). Binance
    is skipped while NEXUS's rate-limit circuit is open (no extra load on a banned IP)."""
    import asyncio as _asyncio

    from nexus_common.binance_guard import blocked
    from nexus_common.proxy import binance_proxy_url
    from nexus_exchange.registry import EXCHANGES, NEXUS_SUPPORT, detect

    out: dict[str, Any] = {}
    for ex in EXCHANGES:
        if ex == "binance":
            spot_until, fut_until = await blocked(redis, "spot"), await blocked(redis, "futures")
            if spot_until is not None:
                out[ex] = {"exchange": ex, "ok": False, "checked_at": ser(utcnow()),
                           "error": f"skipped: Binance paused until {spot_until:%Y-%m-%d %H:%M} "
                                    "UTC (rate limit / IP ban)", "nexus": NEXUS_SUPPORT[ex]}
                continue
            res = await _asyncio.to_thread(detect, ex, proxy_url=binance_proxy_url(),
                                           ca_bundle=settings.extra_ca_file,
                                           futures=fut_until is None)
        else:
            res = await _asyncio.to_thread(detect, ex, ca_bundle=settings.extra_ca_file)
        out[ex] = res
    doc = {"checked_at": ser(utcnow()), "exchanges": out}
    await redis.set(CAPS_KEY, json.dumps(doc, default=str), ex=8 * 86400)
    return doc


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


TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
              "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}
TimeframeParam = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]


async def _public_get(settings: Settings, path: str, params: dict[str, Any],
                      redis: Any = None) -> Any:
    import httpx
    from nexus_common.binance_guard import BinanceBackoff, guarded_get
    from nexus_common.proxy import httpx_kwargs

    base = settings.binance_public_api_url or "https://api.binance.com/api/v3"
    async with httpx.AsyncClient(timeout=15, trust_env=True, **httpx_kwargs()) as client:
        try:
            r = await guarded_get(client, redis, f"{base}/{path}", params, cache_ttl=10)
        except BinanceBackoff as exc:
            raise httpx.HTTPError(str(exc)) from exc
        r.raise_for_status()
        return r.json()


@router.get("/system/binance")
async def binance_status(redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Rate-limit gate (circuit state, last-hour requests by endpoint and status, recent
    events) and the live WebSocket stream state."""
    from nexus_common.binance_guard import status as gate_status
    from nexus_common.proxy import describe as proxy_host
    from nexus_market.binance_ws import STATE_KEY

    out = await gate_status(redis)
    raw = await redis.get(STATE_KEY)
    out["websocket"] = json.loads(raw) if raw else None
    out["egress"] = {"binance_proxy": proxy_host(),
                     "note": "Binance traffic leaves through this static-IP proxy" if proxy_host()
                     else "No proxy: Binance sees Render's shared outbound IPs"}
    return out


def _frames(s: Session, exchange: str, symbol: str, tfs: tuple[str, ...],
            limit: int = 600) -> dict[str, Any]:
    from nexus_strategies.frames import to_frame

    pid = repo.pair_id(s, exchange, symbol)
    return {tf: to_frame(repo.load_candles(s, pid, tf, limit=limit)) for tf in tfs}


@router.get("/analysis/technical")
async def technical(symbol: str = "BTC/USDT", timeframe: TimeframeParam = "1h",
                    s: Session = Depends(db), redis: Any = Depends(redis_dep),
                    settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Indicator snapshot of the latest CLOSED bar (stored candles; nothing estimated)."""
    from nexus_analysis.technical import technical_snapshot

    key = f"nexus:analysis:technical:{symbol}:{timeframe}"
    cached = await redis.get(key)
    if cached:
        return dict(json.loads(cached))
    if symbol not in settings.trading_pairs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not a configured pair")
    try:
        frames = _frames(s, settings.signal_exchange, symbol, (timeframe, "1d"))
    except LookupError:
        return {"symbol": symbol, "timeframe": timeframe, "bars": 0,
                "insufficient_history": True, "note": "no stored candles yet"}
    snap = await asyncio.to_thread(technical_snapshot, frames[timeframe], frames["1d"])
    out = {"symbol": symbol, "timeframe": timeframe, "exchange": settings.signal_exchange,
           **snap}
    await redis.set(key, json.dumps(out, default=str), ex=30)
    return out


@router.get("/analysis/mtf")
async def multi_timeframe(symbol: str = "BTC/USDT", base: TimeframeParam = "5m",
                          s: Session = Depends(db), redis: Any = Depends(redis_dep),
                          settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Per-timeframe trend/momentum/volatility/structure states aligned on the base bar
    (closed bars only) and agreement scores. Features, not trading rules."""
    from nexus_analysis.mtf import MTF_TIMEFRAMES, mtf_snapshot

    key = f"nexus:analysis:mtf:{symbol}:{base}"
    cached = await redis.get(key)
    if cached:
        return dict(json.loads(cached))
    tfs = tuple(dict.fromkeys((*MTF_TIMEFRAMES, base)))
    if symbol not in settings.trading_pairs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not a configured pair")
    try:
        frames = _frames(s, settings.signal_exchange, symbol, tfs)
    except LookupError:
        frames = {}
    frames = {tf: f for tf, f in frames.items() if not f.empty}
    if base not in frames:
        return {"symbol": symbol, "base_timeframe": base, "as_of": None, "timeframes": {},
                "scores": {}, "note": f"no stored {base} candles yet"}
    snap = await asyncio.to_thread(mtf_snapshot, frames, base)
    out = {"symbol": symbol, "exchange": settings.signal_exchange, **snap,
           "missing_timeframes": [tf for tf in MTF_TIMEFRAMES if tf not in frames],
           "note": "Scores are inputs for validated rules, not trade signals by themselves."}
    await redis.set(key, json.dumps(out, default=str), ex=30)
    return out


@router.get("/decision/fusion")
async def decision_fusion(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                          settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """One explained LONG / SHORT / WAIT / NO_TRADE per pair from validated sources only,
    with the strategy selector's verdict for every strategy. Advisory and read-only."""
    from nexus_api import fusion_view

    key = "nexus:decision:fusion"
    cached = await redis.get(key)
    if cached:
        return dict(json.loads(cached))
    values = await fusion_view.redis_inputs(redis, settings)
    out = await asyncio.to_thread(fusion_view.compute, s, values, settings)
    await redis.set(key, json.dumps(out, default=str), ex=60)
    return out


@router.get("/decision/log")
def decision_log(symbol: str | None = Query(None, max_length=32),
                 decision: str | None = Query(None, pattern="^(LONG|SHORT|WAIT|NO_TRADE)$"),
                 limit: int = Query(100, ge=1, le=500),
                 s: Session = Depends(db)) -> dict[str, Any]:
    """Recorded decisions (newest first) with the inputs they used, joined with the
    trader's signal and order for the same pair and bar, if any (the execution result)."""
    q = select(models.DecisionLog).order_by(desc(models.DecisionLog.bar_time),
                                            models.DecisionLog.symbol).limit(limit)
    if symbol:
        q = q.where(models.DecisionLog.symbol == symbol)
    if decision:
        q = q.where(models.DecisionLog.decision == decision)
    rows = s.scalars(q).all()
    out = []
    for r in rows:
        # Trader signals whose bar closed inside this 15m bar, for the same pair.
        sigs = s.execute(
            select(models.TradingSignal, models.Strategy.code)
            .join(models.Strategy, models.Strategy.id == models.TradingSignal.strategy_id)
            .join(models.TradingPair, models.TradingPair.id == models.TradingSignal.pair_id)
            .where(models.TradingPair.symbol == r.symbol,
                   models.TradingSignal.mode == r.mode,
                   models.TradingSignal.bar_time >= r.bar_time,
                   models.TradingSignal.bar_time < r.bar_time + timedelta(minutes=15))).all()
        execution = []
        for sig, code in sigs:
            order = s.scalar(select(models.Order).where(models.Order.signal_id == sig.id)
                             .order_by(models.Order.id).limit(1))
            execution.append({"strategy": code, "signal_status": sig.status,
                              "rejection_reason": sig.rejection_reason,
                              "order_id": order.id if order else None,
                              "order_status": order.status if order else None})
        out.append({
            "id": r.id, "mode": r.mode, "symbol": r.symbol, "bar_time": ser(r.bar_time),
            "decided_at": ser(r.decided_at), "snapshot_id": r.snapshot_id,
            "decision": r.decision, "strategy": r.strategy, "regime": r.regime,
            "risk_state": r.risk_state, "probability": ser(r.probability),
            "expected_return": ser(r.expected_return), "features": r.features,
            "models_used": r.models_used, "strategy_signals": r.strategy_signals,
            "risk_checks": r.risk_checks, "reasons": (r.reasons or {}).get("reasons", []),
            "rejection_reason": r.rejection_reason,
            "execution": execution or None,
            "execution_result": (
                "no trade (nothing to execute)" if r.decision in ("NO_TRADE", "WAIT")
                and not execution else
                "see trader signals" if execution else
                "no trader signal for this bar"),
        })
    return {"decisions": out,
            "note": "Recorded after each closed 15m bar when something changed, at least "
                    "hourly. The decision layer is advisory: only the trader places orders, "
                    "after the independent Risk Manager approves."}


@router.get("/markets/data-quality")
def data_quality(s: Session = Depends(db),
                 settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Coverage, gaps and freshness of every stored candle series (from the database)."""
    from nexus_config import TIMEFRAME_SECONDS
    from nexus_market.derived import QUALITY_WINDOW_BARS, quality_report

    tfs = [tf for tf in TIMEFRAME_SECONDS
           if settings.minute_candles or tf not in ("1m", "3m")]
    pairs = [(e.value, sym) for e in settings.enabled_exchanges for sym in settings.trading_pairs]
    rows = quality_report(s, pairs, tfs)
    return {"window_bars": QUALITY_WINDOW_BARS, "series": [r.as_dict() for r in rows],
            "stale": sum(1 for r in rows if r.stale),
            "note": "Duplicates are impossible by construction (one row per pair, "
                    "timeframe and open time). Derived timeframes are built only from "
                    "complete sets of closed source bars; a gap stays a gap."}


@router.get("/markets/candles")
async def candles(exchange: str, symbol: str,
                  timeframe: TimeframeParam = "1h",
                  limit: int = Query(300, ge=10, le=1000), s: Session = Depends(db),
                  redis: Any = Depends(redis_dep),
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
                                                     "interval": timeframe, "limit": limit + 1},
                                redis)
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
                                          {"symbol": sym.replace("/", "")}, redis)
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
            "time_in_force", "price", "stop_price", "amount", "filled_amount",
            "average_fill_price",
            "expected_price", "fee_amount", "fee_asset", "status", "purpose", "last_error",
            "created_at", "closed_at"]
    return [{**{c: ser(getattr(o, c)) for c in cols}, "exchange": ex, "symbol": sym,
             "post_only": bool((o.details or {}).get("post_only")),
             "triggered": (o.details or {}).get("triggered")
             if o.order_type == "stop_limit" else None}
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


@router.get("/strategies/edge-monitor")
async def edge_monitor_states(s: Session = Depends(db)) -> dict[str, Any]:
    """Edge-decay monitor state per strategy and portfolio (NORMAL / REDUCE / STOP)."""
    names = {p.id: f"{p.name} ({p.mode})" for p in s.scalars(select(models.Portfolio))}
    out = []
    for row in s.scalars(select(models.Strategy)):
        for pid, st in ((row.parameters or {}).get("edge_monitor") or {}).items():
            out.append({"strategy": row.code, "portfolio_id": int(pid),
                        "portfolio": names.get(int(pid)), **st})
    return {"monitors": sorted(out, key=lambda x: (x["strategy"], x["portfolio_id"]))}


class EdgeReset(BaseModel):
    portfolio_id: int
    reason: str = Field(min_length=5, max_length=300)
    totp: str | None = Field(default=None, max_length=6)


@router.post("/strategies/{code}/edge-reset", dependencies=[Depends(owner)])
async def edge_monitor_reset(code: str, body: EdgeReset, s: Session = Depends(db),
                             redis: Any = Depends(redis_dep),
                             viewer: Viewer = Depends(current_viewer),
                             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Owner-only, 2FA, audited: return a strategy's edge monitor to NORMAL. Only trades
    closed after the reset count from then on."""
    from nexus_execution import edge

    await check_totp(viewer, body.totp or "", settings, redis)
    try:
        edge.reset(s, body.portfolio_id, code, actor=viewer.actor, reason=body.reason)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from exc
    return {"ok": True, "strategy": code, "portfolio_id": body.portfolio_id}


OWNER_APPROVAL_CODES = ("bot_ema_rsi_macd",)
OWNER_APPROVAL_ACK = "I accept this strategy has not passed validation"


class OwnerApproval(BaseModel):
    active: bool
    totp: str | None = Field(default=None, max_length=6)
    acknowledgement: str | None = Field(default=None, max_length=120)


@router.post("/strategies/{code}/owner-approval", dependencies=[Depends(owner)])
async def strategy_owner_approval(code: str, body: OwnerApproval, s: Session = Depends(db),
                                  redis: Any = Depends(redis_dep),
                                  viewer: Viewer = Depends(current_viewer),
                                  settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Owner decision to let the main trader run a strategy that has NOT passed validation
    (paper now; live only if every live gate also passes). Enabling needs 2FA and the exact
    acknowledgement; disabling (always safer) needs neither. Audited either way."""
    if code not in OWNER_APPROVAL_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "owner approval is not offered for "
                                                         "this strategy")
    row = s.scalar(select(models.Strategy).where(models.Strategy.code == code))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if body.active:
        if body.acknowledgement != OWNER_APPROVAL_ACK:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f'type exactly: "{OWNER_APPROVAL_ACK}"')
        await check_totp(viewer, body.totp or "", settings, redis)
    now = utcnow()
    params = dict(row.parameters or {})
    params["owner_override"] = {"active": body.active, "actor": viewer.actor, "at": now.isoformat()}
    row.parameters = params
    row.is_enabled = body.active
    repo.record_audit(s, actor=viewer.actor, user_id=viewer.user.id,
                      action="strategy_owner_approval", target_type="strategy",
                      target_id=code, details={"active": body.active})
    return {"ok": True, "code": code, "active": body.active, "at": ser(now)}


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

    def load() -> tuple[Any, dict[str, Any]]:
        ev = s.scalars(select(models.SystemEvent).order_by(desc(models.SystemEvent.id))
                       .limit(100)).all()
        counts = {t: s.scalar(select(func.count()).select_from(getattr(models, m))) for t, m in (
            ("candles", "MarketCandle"), ("orders", "Order"), ("signals", "TradingSignal"))}
        return ev, counts

    ev, counts = await asyncio.to_thread(load)   # blocking queries off the event loop
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


class RiskFloorBody(BaseModel):
    mode: Literal["paper", "live"]
    state: Literal["NORMAL", "REDUCED_RISK", "NO_NEW_TRADES", "CLOSE_ONLY"]
    reason: str = Field(min_length=5, max_length=500)
    totp: str | None = Field(default=None, max_length=6)


@router.get("/risk/state", dependencies=[Depends(owner)])
async def risk_state(s: Session = Depends(db), redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Current risk state per mode as derived by the trader (refreshed every cycle), the
    owner's minimum state, the last recorded transition, and balance reconciliation."""
    from nexus_execution.reconcile import STATUS_KEY
    from nexus_execution.trader import RISK_STATE_KEY

    out: dict[str, Any] = {}
    for mode in ("paper", "live"):
        raw = await redis.get(RISK_STATE_KEY.format(mode=mode))
        recon = await redis.get(STATUS_KEY.format(mode=mode))
        port = s.scalar(select(models.Portfolio.id).where(models.Portfolio.mode == mode,
                                                          models.Portfolio.name == "main"))
        out[mode] = {
            "current": json.loads(raw) if raw else None,
            "owner_floor": repo.risk_floor_db_state(s, mode) or "NORMAL",
            "last_transition": repo.last_risk_state(s, port) if port else None,
            "reconciliation": json.loads(recon) if recon else None,
        }
    return out


@router.post("/risk/state/floor", dependencies=[Depends(owner)])
async def set_risk_floor(body: RiskFloorBody, s: Session = Depends(db),
                         redis: Any = Depends(redis_dep),
                         viewer: Viewer = Depends(current_viewer),
                         settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Set the minimum risk state. Making it stricter is always allowed; relaxing it in
    live mode needs 2FA. Conditions the trader detects (loss limits, kill switch,
    reconciliation) still apply on top: the floor can only add restrictions."""
    from nexus_risk.state import SEVERITY, RiskState

    current = RiskState(repo.risk_floor_db_state(s, body.mode) or "NORMAL")
    target = RiskState(body.state)
    if SEVERITY[target] < SEVERITY[current] and body.mode == "live":
        await check_totp(viewer, body.totp, settings, redis)
    repo.record_audit(s, actor=viewer.actor, action=repo.RISK_FLOOR_ACTION,
                      user_id=viewer.user.id,
                      details={"mode": body.mode, "state": target.value,
                               "previous": current.value, "reason": body.reason})
    return {"mode": body.mode, "owner_floor": target.value,
            "note": "Applied by the trader on its next cycle (about 30 s)."}


LIVE_MODE_PHRASE = "ENABLE LIVE AUTONOMOUS TRADING"
FUND_PHRASE = "FUND LIVE LEDGER"


class OperatingModeBody(BaseModel):
    mode: Literal["ANALYSIS_ONLY", "SIGNALS_ONLY", "PAPER_TRADING", "LIVE_AUTONOMOUS"]
    reason: str = Field(min_length=5, max_length=500)
    confirm: str | None = Field(default=None, max_length=64)
    totp: str | None = Field(default=None, max_length=6)


async def _live_mode_blockers(s: Session, redis: Any, settings: Settings) -> list[str]:
    """Everything that must hold before LIVE_AUTONOMOUS can be selected."""
    from nexus_execution.live_gate import live_gate_problems
    from nexus_execution.reconcile import STATUS_KEY
    from nexus_execution.trader import RISK_STATE_KEY

    problems = await live_gate_problems(settings, s, redis,
                                        [e.value for e in settings.enabled_exchanges])
    recon = await redis.get(STATUS_KEY.format(mode="live"))
    if recon is None:
        problems.append("no live balance reconciliation yet (live trader not running)")
    elif json.loads(recon).get("status") != "ok":
        problems.append(f"live balance reconciliation is {json.loads(recon).get('status')}")
    risk = await redis.get(RISK_STATE_KEY.format(mode="live"))
    if risk is None:
        problems.append("live risk state not reported yet")
    elif not json.loads(risk).get("entries_allowed"):
        problems.append(f"live risk state is {json.loads(risk).get('state')}")
    validated = [st for st in s.scalars(select(models.Strategy).where(models.Strategy.is_enabled))
                 if ((st.parameters or {}).get("validation") or {}).get("passed")]
    if not validated:
        problems.append("no enabled strategy has passed validation")
    port = s.scalar(select(models.Portfolio.id).where(models.Portfolio.mode == "live",
                                                      models.Portfolio.name == "main"))
    funded = port and s.scalar(select(func.count()).select_from(models.Balance).where(
        models.Balance.portfolio_id == port, models.Balance.source == "internal",
        models.Balance.free + models.Balance.locked > 0))
    if not funded:
        problems.append("live ledger has no funds (confirm a starting balance first)")
    return problems


@router.get("/settings/operating-mode", dependencies=[Depends(owner)])
async def get_operating_mode(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    from nexus_common.opmode import OperatingMode, parse, places_orders

    row = repo.operating_mode_db_state(s)
    op = parse(row.get("mode") if row else None)
    return {"mode": op.value, "set_by": row.get("actor") if row else None,
            "at": row.get("at") if row else None, "reason": row.get("reason") if row else None,
            "server_trading_mode": settings.trading_mode.value,
            "paper_orders": places_orders(op, "paper"), "live_orders": places_orders(op, "live"),
            "modes": [m.value for m in OperatingMode],
            "live_blockers": await _live_mode_blockers(s, redis, settings),
            "live_confirm_phrase": LIVE_MODE_PHRASE}


@router.post("/settings/operating-mode", dependencies=[Depends(owner)])
async def set_operating_mode(body: OperatingModeBody, s: Session = Depends(db),
                             redis: Any = Depends(redis_dep),
                             viewer: Viewer = Depends(current_viewer),
                             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Switching to a less active mode is always allowed. LIVE_AUTONOMOUS needs the exact
    phrase, 2FA and every live gate passing (it is refused today by design)."""
    from nexus_common.opmode import AUDIT_ACTION, OperatingMode

    if body.mode == OperatingMode.LIVE_AUTONOMOUS.value:
        if body.confirm != LIVE_MODE_PHRASE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"type exactly: {LIVE_MODE_PHRASE}")
        await check_totp(viewer, body.totp, settings, redis)
        blockers = await _live_mode_blockers(s, redis, settings)
        if blockers:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "live autonomous trading not permitted: " + "; ".join(blockers))
    previous = repo.operating_mode_db_state(s)
    repo.record_audit(s, actor=viewer.actor, action=AUDIT_ACTION, user_id=viewer.user.id,
                      details={"mode": body.mode, "reason": body.reason,
                               "previous": previous.get("mode") if previous else None})
    return {"mode": body.mode, "note": "Applied by the trader on its next cycle (about 30 s)."}


class MarketAutonomyBody(BaseModel):
    autonomy: Literal["OFF", "RESEARCH", "PAPER", "LIVE"]
    reason: str = Field(min_length=5, max_length=500)
    confirm: str | None = Field(default=None, max_length=64)
    totp: str | None = Field(default=None, max_length=6)


@router.get("/markets", dependencies=[Depends(owner)])
async def markets_overview(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                           settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Every market with its autonomy switch, what it supports, its venues and connector
    status, and (crypto) the paper book's open and closed positions. Nothing is shown as
    connected unless NEXUS has a working connector for it."""
    from nexus_backtest import multiasset_paper as mp
    from nexus_common.markets import (
        MARKETS,
        Autonomy,
        Market,
        combine,
        describe,
        effective,
        live_phrase,
    )
    from nexus_common.opmode import parse as parse_op
    from nexus_common.opmode import places_orders

    rows = repo.market_autonomy_db_state(s)
    op_row = repo.operating_mode_db_state(s)
    op = parse_op(op_row.get("mode") if op_row else None)
    crypto_blockers = await _live_mode_blockers(s, redis, settings)
    raw_research = await redis.get(mp.STATUS_KEY)
    research = json.loads(raw_research) if raw_research else None
    from nexus_api.bridge import STATUS_KEY as EXNESS_KEY

    raw_exness = await redis.get(EXNESS_KEY)
    exness = json.loads(raw_exness) if raw_exness else None
    if exness:
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        seen = _dt.fromisoformat(exness["at"])
        exness["connected"] = (_dt.now(_UTC) - seen).total_seconds() < 900
    fwd_rows = s.scalars(select(models.ForwardTestEvent).where(
        models.ForwardTestEvent.run == mp.RUN).order_by(models.ForwardTestEvent.at)).all()
    stocks_forward = mp.summary([r.data for r in fwd_rows if r.kind == "day"],
                                sum(1 for r in fwd_rows if r.kind == "divergence"))
    positions = {k: int(v) for k, v in s.execute(
        select(models.Position.status, func.count()).where(models.Position.mode == "paper")
        .group_by(models.Position.status)).all()}
    out = []
    for d in describe():
        mk = Market(d["market"])
        auto = effective(mk, rows)
        row = rows.get(mk.value) or {}
        eff = combine(op, auto)
        d.update(autonomy=auto.value, set_by=row.get("actor"), at=row.get("at"),
                 reason=row.get("reason"), live_confirm_phrase=live_phrase(mk),
                 effective_mode=eff.value,
                 paper_orders=places_orders(eff, "paper"), live_orders=places_orders(eff, "live"))
        if mk is Market.CRYPTO:
            d["live_blockers"] = crypto_blockers
            d["paper_positions"] = {"open": positions.get("open", 0),
                                    "closed": positions.get("closed", 0)}
        else:
            d["live_blockers"] = MARKETS[mk].missing_for(Autonomy.LIVE)
            d["paper_positions"] = None
            d["research"] = ((research or {}).get("markets") or {}).get(mk.value)
            if exness:
                cls = {"stocks": "stocks", "forex": "forex", "commodities": "commodities"}
                d["exness"] = {**{k: v for k, v in exness.items() if k != "symbols"},
                               "symbols": [x for x in exness.get("symbols", [])
                                           if x.get("class") == cls[mk.value]]}
            d["research_at"] = (research or {}).get("at")
            if mk is Market.STOCKS:
                d["paper_forward"] = stocks_forward
        out.append(d)
    return {"operating_mode": op.value, "markets": out, "exness": exness,
            "server_trading_mode": settings.trading_mode.value}


class BridgeTokenBody(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9 ._-]+$")
    reason: str = Field(min_length=5, max_length=500)


@router.get("/bridge-tokens", dependencies=[Depends(owner)])
async def list_bridge_tokens(s: Session = Depends(db)) -> dict[str, Any]:
    rows = s.scalars(select(models.BrokerBridge).order_by(models.BrokerBridge.id.desc())).all()
    return {"bridges": [{"id": b.id, "name": b.name, "venue": b.venue,
                         "created_at": b.created_at.isoformat() if b.created_at else None,
                         "last_seen_at": b.last_seen_at.isoformat() if b.last_seen_at else None,
                         "revoked": b.revoked_at is not None} for b in rows]}


@router.post("/bridge-tokens", dependencies=[Depends(owner)])
async def create_bridge_token(body: BridgeTokenBody, s: Session = Depends(db),
                              viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """A token for a read-only Exness MT5 bridge. Shown ONCE; only its hash is stored."""
    import secrets

    from nexus_api.bridge import token_hash

    token = "nxb_" + secrets.token_urlsafe(32)
    bridge = models.BrokerBridge(name=body.name, venue="exness_mt5",
                                 token_hash=token_hash(token), created_by=viewer.user.id)
    s.add(bridge)
    s.flush()
    repo.record_audit(s, actor=viewer.actor, action="bridge_token_created", user_id=viewer.user.id,
                      target_type="broker_bridge", target_id=str(bridge.id),
                      details={"name": body.name, "venue": "exness_mt5", "reason": body.reason})
    return {"id": bridge.id, "token": token,
            "note": "Copy it now: it is shown only once. Put it in the bridge's settings on "
                    "your Windows machine, never in chat or email."}


@router.get("/research/history", dependencies=[Depends(owner)])
async def research_history_manifest(s: Session = Depends(db)) -> dict[str, Any]:
    """Broker price history received from the Exness bridge for research, per series."""
    rows = s.execute(select(
        models.HistoryChunk.symbol, models.HistoryChunk.timeframe, func.count(),
        func.sum(models.HistoryChunk.bar_count), func.min(models.HistoryChunk.first_open),
        func.max(models.HistoryChunk.last_open),
        func.sum(func.octet_length(models.HistoryChunk.data)),
        func.min(models.HistoryChunk.server_utc_offset_hours),
        func.max(models.HistoryChunk.server_utc_offset_hours)).group_by(
        models.HistoryChunk.symbol, models.HistoryChunk.timeframe).order_by(
        models.HistoryChunk.symbol, models.HistoryChunk.timeframe)).all()
    return {"series": [{"symbol": r[0], "timeframe": r[1], "chunks": r[2], "bars": int(r[3]),
                        "first_open": r[4].isoformat(), "last_open": r[5].isoformat(),
                        "bytes": int(r[6]), "server_utc_offsets": sorted({r[7], r[8]})}
                       for r in rows],
            "note": "Bars are the broker's own prices, converted to UTC. Nothing here was "
                    "generated or filled in by NEXUS."}


@router.get("/research/history/{symbol}/{timeframe}.csv", dependencies=[Depends(owner)])
async def research_history_csv(symbol: str, timeframe: str,
                               s: Session = Depends(db)) -> Response:
    from nexus_api.bridge import history_text

    if not re.fullmatch(r"[A-Z]{6}", symbol) or timeframe not in ("M5", "H1", "D1"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown series")
    text_ = history_text(s, symbol, timeframe)
    if text_ is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no history for this series")
    return Response(text_, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{symbol}_{timeframe}.csv"'})


@router.post("/bridge-tokens/{bridge_id}/revoke", dependencies=[Depends(owner)])
async def revoke_bridge_token(bridge_id: int, s: Session = Depends(db),
                              viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    from nexus_common.timeutil import utcnow as _now

    bridge = s.get(models.BrokerBridge, bridge_id)
    if bridge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown bridge")
    if bridge.revoked_at is None:
        bridge.revoked_at = _now()
        repo.record_audit(s, actor=viewer.actor, action="bridge_token_revoked",
                          user_id=viewer.user.id, target_type="broker_bridge",
                          target_id=str(bridge.id), details={"name": bridge.name})
    return {"id": bridge.id, "revoked": True}


@router.get("/research/multi-asset")
async def multi_asset_research() -> dict[str, Any]:
    """Multi-asset Stage 3: the committed pre-registered results (forex and NASDAQ-100
    trend following; volatility-managed NASDAQ-100)."""
    from pathlib import Path

    import nexus_backtest

    def load(name: str) -> Any:
        f = Path(nexus_backtest.__file__).with_name("results") / name
        return json.loads(f.read_text()) if f.exists() else None

    def latest(prefix: str) -> Any:
        """The newest ``<prefix>_YYYY-MM.json`` result (not its data-quality files)."""
        files = sorted(Path(nexus_backtest.__file__).with_name("results").glob(
            f"{prefix}_[0-9][0-9][0-9][0-9]-[0-9][0-9].json"))
        return json.loads(files[-1].read_text()) if files else None

    nqs1 = await asyncio.to_thread(latest, "nqs1")
    night = await asyncio.to_thread(latest, "night_scalper")
    bgrid = await asyncio.to_thread(latest, "binance_grid")
    league = await asyncio.to_thread(latest, "bot_league")
    exvol = await asyncio.to_thread(latest, "exness_voltarget")
    return {"trend": await asyncio.to_thread(load, "multi_asset_trend_2026-11.json"),
            "ndx_voltarget": await asyncio.to_thread(load, "ndx_voltarget_2026-11.json"),
            "fx_styles": await asyncio.to_thread(load, "fx_styles_2026-11.json"),
            "nqs1": {"status": "result" if nqs1 else "waiting_for_data", "result": nqs1,
                     "preregistration": "docs/research/2026-11-27-nqs1-preregistration.md"},
            "night_scalper": night,
            "binance_grid": bgrid,
            "bot_league": league,
            "exness_voltarget": exvol,
            "preregistrations": [
                "docs/research/2026-11-26-multi-asset-trend-preregistration.md",
                "docs/research/2026-11-26-ndx-voltarget-preregistration.md",
                "docs/research/2026-11-26-fx-styles-preregistration.md",
                "docs/research/2026-11-27-nqs1-preregistration.md",
                "docs/research/2026-11-27-night-scalper-preregistration.md",
                "docs/research/2026-11-27-binance-spot-grid-preregistration.md",
                "docs/research/2026-11-27-exness-voltarget-preregistration.md"]}


@router.post("/markets/{market}/autonomy", dependencies=[Depends(owner)])
async def set_market_autonomy(market: str, body: MarketAutonomyBody, s: Session = Depends(db),
                              redis: Any = Depends(redis_dep),
                              viewer: Viewer = Depends(current_viewer),
                              settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Set one market's switch. A less active mode is always allowed (if the market
    supports it). LIVE needs that market's exact phrase, 2FA and its live gates."""
    from nexus_common.markets import AUDIT_ACTION, MARKETS, Autonomy, Market, live_phrase

    try:
        mk = Market(market)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown market") from None
    want = Autonomy(body.autonomy)
    missing = MARKETS[mk].missing_for(want)
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{mk.value} cannot be set to {want.value}: " + "; ".join(missing))
    if want is Autonomy.LIVE:
        if body.confirm != live_phrase(mk):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"type exactly: {live_phrase(mk)}")
        await check_totp(viewer, body.totp, settings, redis)
        blockers = await _live_mode_blockers(s, redis, settings) if mk is Market.CRYPTO else \
            ["no live connector"]
        if blockers:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"live trading for {mk.value} not permitted: " + "; ".join(blockers))
    previous = repo.market_autonomy_db_state(s).get(mk.value)
    repo.record_audit(s, actor=viewer.actor, action=AUDIT_ACTION, user_id=viewer.user.id,
                      target_type="market", target_id=mk.value,
                      details={"autonomy": want.value, "reason": body.reason,
                               "previous": previous.get("autonomy") if previous else None})
    return {"market": mk.value, "autonomy": want.value,
            "note": "Applied by the workers on their next cycle (about 30 s). Open positions "
                    "stay protected."}


class FundLiveBody(BaseModel):
    amount: Decimal = Field(gt=0)
    asset: str = Field(default="USDT", pattern=r"^[A-Z0-9]{2,10}$")
    confirm: str = Field(max_length=64)
    totp: str | None = Field(default=None, max_length=6)


@router.post("/live/ledger/fund", dependencies=[Depends(owner)])
async def fund_live(body: FundLiveBody, s: Session = Depends(db), redis: Any = Depends(redis_dep),
                    viewer: Viewer = Depends(current_viewer),
                    settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Let the live trader manage part of the USDT that Binance reports as free. Bounded by
    the latest reconciled exchange report; moves nothing on the exchange."""
    from nexus_execution.order_manager import fund_live_ledger

    if body.confirm != FUND_PHRASE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"type exactly: {FUND_PHRASE}")
    await check_totp(viewer, body.totp, settings, redis)
    port = s.scalar(select(models.Portfolio.id).where(models.Portfolio.mode == "live",
                                                      models.Portfolio.name == "main"))
    if port is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "no live portfolio yet: the live trader creates it on first start")
    try:
        total = fund_live_ledger(s, port, repo.exchange_id(s, "binance"), body.asset,
                                 body.amount)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    repo.record_audit(s, actor=viewer.actor, action="live_ledger_funded", user_id=viewer.user.id,
                      target_type="portfolio", target_id=str(port),
                      details={"asset": body.asset, "amount": str(body.amount),
                               "ledger_total": str(total)})
    return {"asset": body.asset, "ledger_total": str(total)}


class CredentialBody(BaseModel):
    exchange: Literal["binance", "kraken", "coinbase"]
    label: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=8, max_length=4096)
    api_secret: str = Field(min_length=8, max_length=8192)
    withdrawals_disabled_confirmed: bool
    totp: str = Field(min_length=6, max_length=6)
    environment: Literal["testnet", "production"] = "testnet"
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
        environment="sandbox" if body.environment == "testnet" else "production",
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
            # Demo keys are no longer supported (owner decision 2026-11-23); an old one is
            # still listed as such so it can be seen and revoked.
            "environment": {"sandbox": "testnet", "demo": "demo (not supported)"}.get(
                c.environment, "production"),
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


@router.get("/research/freqtrade-community")
async def freqtrade_community() -> dict[str, Any]:
    """Out-of-sample test of the public Freqtrade strategy collection (pre-registered
    2026-11-23; one run). A backtest on historical data: no trade was placed."""
    from nexus_backtest.freqtrade_community import load_result

    res = await asyncio.to_thread(load_result)
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no result recorded yet")
    return res


AgridAction = Literal["start", "pause", "resume", "stop_all", "emergency_shutdown"]


class AgridControlBody(BaseModel):
    action: AgridAction
    symbol: str | None = Field(default=None, max_length=24, pattern=r"^[A-Z0-9]{2,24}$")
    reason: str = Field(min_length=5, max_length=300)


class AgridLimitsBody(BaseModel):
    limits: dict[str, Any]
    reason: str = Field(min_length=5, max_length=300)


@router.get("/adaptive-grid")
async def adaptive_grid(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                        settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Adaptive grid system (PAPER ONLY): live status, recent fills and decisions, and
    performance computed from the append-only ledger."""
    from nexus_execution.adaptive_grid_paper import STATUS_KEY

    raw = await redis.get(STATUS_KEY)

    def history() -> dict[str, Any]:
        E = models.AdaptiveGridEvent

        def rows(kinds: tuple[str, ...], n: int) -> list[dict[str, Any]]:
            q = select(E).where(E.kind.in_(kinds)).order_by(desc(E.id)).limit(n)
            return [{"seq": r.seq, "at": ser(r.at), "account": r.account, "kind": r.kind,
                     "grid_id": r.grid_id, "symbol": r.symbol,
                     **{k: v for k, v in r.data.items() if k != "state"}}
                    for r in s.scalars(q).all()]
        perf: dict[str, dict[str, Any]] = {}
        for r in s.scalars(select(E).where(E.kind.in_(("fill", "funding")))).all():
            key = f"{r.account}:{r.symbol}"
            p = perf.setdefault(key, {"market": r.account, "symbol": r.symbol, "fills": 0,
                                      "round_trip_exits": 0, "realized": 0.0, "fees": 0.0,
                                      "funding": 0.0, "wins": 0, "losses": 0})
            if r.kind == "funding":
                p["funding"] += float(r.data.get("paid") or 0)
                continue
            p["fills"] += 1
            p["fees"] += float(r.data.get("fee") or 0)
            if r.data.get("pnl") is not None:
                pnl = float(r.data["pnl"])
                p["realized"] += pnl
                p["round_trip_exits"] += 1
                p["wins" if pnl > 0 else "losses"] += 1
        for p in perf.values():
            p["net_after_costs"] = p["realized"] - p["fees"] - p["funding"]
        return {"fills": rows(("fill",), 100),
                "decisions": rows(("decision", "regime", "grid_start", "grid_refused",
                                   "suspend", "resume", "stop", "retire", "risk", "control",
                                   "funding", "gap"), 100),
                "performance": sorted(perf.values(), key=lambda p: -p["net_after_costs"])}

    def backtest() -> dict[str, Any] | None:
        from pathlib import Path

        import nexus_backtest

        f = Path(nexus_backtest.__file__).with_name("results") / "adaptive_grid_2026-11.json"
        if not f.exists():
            return None
        r = json.loads(f.read_text())
        return {"passed": r["passed"], "gates": r["gates"], "oos": r["oos_walk_forward"],
                "by_regime": r["by_regime_oos"], "by_symbol": r["by_symbol_oos"],
                "sensitivity": r["sensitivity_full_window"],
                "stress_2x_fees": r["stress_2x_fees_default_set"],
                "buy_and_hold": r["buy_and_hold"],
                "preregistration": "docs/research/2026-11-26-adaptive-grid-preregistration.md"}

    return {"mode": settings.agrid_mode, "paper": True,
            "status": json.loads(raw) if raw else None, **await asyncio.to_thread(history),
            "backtest": await asyncio.to_thread(backtest),
            "note": "PAPER TRADING: simulated orders and balances on live Binance market "
                    "data. No real order can be placed by this system."}


@router.get("/adaptive-grid/universe")
async def adaptive_grid_universe(s: Session = Depends(db)) -> dict[str, Any]:
    """The latest dynamic-universe scan per market: selected, ranked and rejected."""
    def load() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for market in ("spot", "futures"):
            r = s.scalar(select(models.GridUniverseScan)
                         .where(models.GridUniverseScan.market == market)
                         .order_by(desc(models.GridUniverseScan.id)).limit(1))
            out[market] = None if r is None else {
                "at": ser(r.at), "selected": r.data.get("selected", []),
                "ranked": r.data.get("ranked", []),
                "symbols_seen": r.data.get("symbols_seen"),
                "rejected_count": len(r.data.get("rejected_stage_one", []))}
        return out
    return await asyncio.to_thread(load)


@router.post("/adaptive-grid/control", dependencies=[Depends(owner)])
async def adaptive_grid_control(body: AgridControlBody, s: Session = Depends(db),
                                redis: Any = Depends(redis_dep),
                                viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """Owner control of the PAPER grid system (audited). None of these can enable live
    trading: the system has no live path."""
    from nexus_execution.adaptive_grid_paper import COMMANDS_KEY

    cmd = {"action": body.action, "symbol": body.symbol, "by": viewer.actor,
           "reason": body.reason}
    await redis.rpush(COMMANDS_KEY, json.dumps(cmd))
    repo.record_audit(s, actor=viewer.actor, action=f"agrid_{body.action}",
                      user_id=viewer.user.id, target_type="adaptive_grid",
                      details={**cmd, "paper": True})
    return {"queued": cmd}


@router.post("/adaptive-grid/limits", dependencies=[Depends(owner)])
async def adaptive_grid_limits(body: AgridLimitsBody, s: Session = Depends(db),
                               redis: Any = Depends(redis_dep),
                               viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """Set the grid risk limits (only within the hard ceilings; audited)."""
    from nexus_execution.adaptive_grid_paper import COMMANDS_KEY, LIMITS_KEY
    from nexus_risk.grid_risk import GridRiskLimits

    try:
        lim = GridRiskLimits.from_json(body.limits)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid limit value") \
            from exc
    problems = lim.problems()
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "; ".join(problems))
    await redis.set(LIMITS_KEY, json.dumps(lim.to_json()))
    await redis.rpush(COMMANDS_KEY, json.dumps({"action": "set_limits",
                                                "limits": lim.to_json()}))
    repo.record_audit(s, actor=viewer.actor, action="agrid_limits", user_id=viewer.user.id,
                      target_type="adaptive_grid",
                      details={"limits": lim.to_json(), "reason": body.reason})
    return {"limits": lim.to_json()}


class GridRestartBody(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.get("/grid-bot")
async def grid_bot(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                   settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """PAPER grid bot: the grid, resting orders, simulated fills and P&L. No real order."""
    from nexus_execution.grid_paper import STATUS_KEY

    raw = await redis.get(STATUS_KEY)

    def history() -> list[dict[str, Any]]:
        rows = s.scalars(select(models.GridPaperEvent)
                         .where(models.GridPaperEvent.kind.in_(("start", "restart", "fill",
                                                                "stop", "mark")))
                         .order_by(desc(models.GridPaperEvent.id)).limit(200)).all()
        return [{"run": r.run, "kind": r.kind, "at": ser(r.at),
                 **{k: v for k, v in r.data.items() if k not in ("state", "config")}}
                for r in rows]

    return {"mode": settings.grid_bot_mode, "paper": True,
            "status": json.loads(raw) if raw else None,
            "history": await asyncio.to_thread(history),
            "note": "Simulated money on the live Binance order book. No real order is "
                    "placed."}


@router.post("/grid-bot/restart", dependencies=[Depends(owner)])
async def grid_restart(body: GridRestartBody, s: Session = Depends(db),
                       redis: Any = Depends(redis_dep),
                       viewer: Viewer = Depends(current_viewer)) -> dict[str, Any]:
    """Owner restart after a stop-loss: a new grid is built from the current market. The
    bot never restarts itself after a stop (no automatic recovery trading)."""
    from nexus_execution.grid_paper import RESTART_KEY

    await redis.set(RESTART_KEY, viewer.actor, ex=600)
    repo.record_audit(s, actor=viewer.actor, action="grid_restart", user_id=viewer.user.id,
                      target_type="grid_bot", details={"reason": body.reason, "paper": True})
    return {"requested": True}


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


@router.get("/research/carry-paper")
def carry_paper_status(s: Session = Depends(db)) -> dict[str, Any]:
    """Forward paper test of always-on funding carry (pre-registered 2026-11-22)."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_market.carry_paper import RUN, report

    rows = s.scalars(select(models.CarryPaperEvent).where(models.CarryPaperEvent.run == RUN)
                     .order_by(models.CarryPaperEvent.id)).all()
    events = [{"id": r.id, "kind": r.kind, "symbol": r.symbol, "at": r.at, "data": r.data}
              for r in rows]
    out = report(events, _dt.now(UTC))
    out["recent_events"] = [{"at": ser(r.at), "symbol": r.symbol, "kind": r.kind,
                             "detail": {k: v for k, v in r.data.items() if k != "state"}}
                            for r in rows if r.kind in ("entry", "funding", "rebalance", "gap")][-30:]
    out["preregistration"] = "docs/research/2026-11-22-carry-forward-paper-preregistration.md"
    return out


@router.get("/research/taker-forward")
async def taker_forward_status(s: Session = Depends(db),
                               redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Forward paper test of taker flow in range regimes (pre-registered 2026-11-22)."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_backtest.forward_taker import RUN, STATUS_KEY, summary

    rows = s.scalars(select(models.ForwardTestEvent).where(models.ForwardTestEvent.run == RUN)
                     .order_by(models.ForwardTestEvent.at)).all()
    trades = [{**r.data, "symbol": r.symbol} for r in rows if r.kind == "trade"]
    gaps = sum(1 for r in rows if r.kind == "gap")
    raw = await redis.get(STATUS_KEY)
    return {**summary(trades, gaps, _dt.now(UTC)), "trades_list": trades[-50:],
            "status": json.loads(raw) if raw else None,
            "preregistration": "docs/research/2026-11-22-taker-range-forward-preregistration.md"}


@router.get("/spot-engine")
async def spot_engine(s: Session = Depends(db), redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Spot strategy engine (paper only): live regime/signal status, the latest signals with
    their checks and rejection reasons, open paper positions, closed-trade performance and
    the 180-day validation status. LIVE TRADING IS DISABLED for this engine."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_execution.spot_paper import BACKTEST, PORTFOLIO, STATUS_KEY

    raw = await redis.get(STATUS_KEY)
    p = s.scalar(select(models.Portfolio).where(models.Portfolio.name == PORTFOLIO,
                                                models.Portfolio.mode == "paper"))
    out: dict[str, Any] = {"live_trading": "DISABLED", "status": json.loads(raw) if raw else None,
                           "portfolio": None, "backtest": BACKTEST}
    sig = s.execute(select(models.TradingSignal, models.Strategy.code, models.TradingPair.symbol)
                    .join(models.Strategy, models.Strategy.id == models.TradingSignal.strategy_id)
                    .join(models.TradingPair, models.TradingPair.id == models.TradingSignal.pair_id)
                    .where(models.Strategy.code.in_(("trend_pullback", "breakout_retest_v2")),
                           models.TradingSignal.mode == "paper")
                    .order_by(desc(models.TradingSignal.id)).limit(100)).all()
    out["signals"] = [{"time": ser(x.generated_at), "bar_time": ser(x.bar_time), "strategy": code,
                       "symbol": sym, "timeframe": x.timeframe, "entry": ser(x.entry_price),
                       "stop": ser(x.stop_loss), "target": ser(x.take_profit),
                       "score": ser(x.confidence), "status": x.status,
                       "reason": x.rejection_reason, "details": x.details}
                      for x, code, sym in sig]
    if p is None:
        return out
    rows = s.execute(select(models.Position, models.TradingPair.symbol)
                     .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
                     .where(models.Position.portfolio_id == p.id)
                     .order_by(models.Position.opened_at)).all()
    closed = [(x, sym) for x, sym in rows if x.status == "closed"]
    pnl = [float(x.realized_pnl) for x, _ in closed]
    wins, losses = [v for v in pnl if v > 0], [-v for v in pnl if v <= 0]
    started = p.created_at
    days = (_dt.now(UTC) - started).days if started else 0
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None
    fees = float(s.scalar(select(func.coalesce(func.sum(models.Order.fee_amount), 0))
                          .where(models.Order.portfolio_id == p.id)) or 0)
    out["portfolio"] = {"id": p.id, "started_at": ser(started), "days": days,
                        "initial_capital": ser(p.initial_capital)}
    out["open_positions"] = [{"symbol": sym, "quantity": ser(x.quantity),
                              "entry": ser(x.avg_entry_price), "stop": ser(x.stop_loss),
                              "target": ser(x.take_profit), "opened_at": ser(x.opened_at),
                              "realized_pnl": ser(x.realized_pnl)}
                             for x, sym in rows if x.status == "open"]
    out["performance"] = {"closed_trades": len(pnl), "net_pnl": sum(pnl) if pnl else 0.0,
                          "win_rate": len(wins) / len(pnl) if pnl else None,
                          "profit_factor": pf,
                          "expectancy": sum(pnl) / len(pnl) if pnl else None, "fees": fees}
    out["validation"] = {
        "required_days": 180, "days": days, "required_trades": 200, "trades": len(pnl),
        "profit_factor_min": 1.30, "max_drawdown_max": 0.10,
        "checks": {"days": days >= 180, "trades": len(pnl) >= 200,
                   "profit_factor": pf is not None and pf >= 1.30,
                   "expectancy_positive": bool(pnl) and sum(pnl) > 0},
        "eligible_for_live": False,
        "note": "Live trading stays disabled. Even if every check passes, live use needs a "
                "separately reviewed implementation and the owner's manual activation."}
    return out


@router.get("/research/trend-forward")
async def trend_forward_status(s: Session = Depends(db),
                               redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Forward paper test of the crypto trends ensemble (pre-registered 2026-11-23)."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_backtest.trend_paper import RUN, STATUS_KEY, summary

    rows = s.scalars(select(models.ForwardTestEvent).where(models.ForwardTestEvent.run == RUN)
                     .order_by(models.ForwardTestEvent.at)).all()
    days = [r.data for r in rows if r.kind == "day"]
    divergences = sum(1 for r in rows if r.kind == "divergence")
    gaps = [r.key for r in rows if r.kind == "gap"]
    raw = await redis.get(STATUS_KEY)
    return {**summary(days, divergences, gaps, _dt.now(UTC)),
            "status": json.loads(raw) if raw else None,
            "preregistration": "docs/research/2026-11-23-crypto-trends-forward-preregistration.md",
            "backtest": "docs/research/2026-11-23-crypto-trends-preregistration.md"}


@router.get("/research/rebalancing")
async def rebalancing_status(s: Session = Depends(db),
                             redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Rebalancing bot and cross-sectional momentum: the committed pre-registered result,
    and the PAPER forward test of the basket that passed (nothing is executed)."""
    from datetime import UTC
    from datetime import datetime as _dt
    from pathlib import Path

    import nexus_backtest
    from nexus_backtest.rebalance_paper import RUN, STATUS_KEY, summary

    def load() -> dict[str, Any]:
        f = Path(nexus_backtest.__file__).with_name("results") / \
            "rebalancing_xsmom_2026-11.json"
        bt = json.loads(f.read_text()) if f.exists() else None
        rows = s.scalars(select(models.ForwardTestEvent).where(
            models.ForwardTestEvent.run == RUN).order_by(models.ForwardTestEvent.at)).all()
        days = [r.data for r in rows if r.kind == "day"]
        div = sum(1 for r in rows if r.kind == "divergence")
        gaps = [r.key for r in rows if r.kind == "gap"]
        return {"backtest": bt, "forward": summary(days, div, gaps, _dt.now(UTC))}

    out = await asyncio.to_thread(load)
    raw = await redis.get(STATUS_KEY)
    out["forward"]["status"] = json.loads(raw) if raw else None
    out["preregistration"] = "docs/research/2026-11-26-rebalancing-xsmom-preregistration.md"
    return out


@router.get("/research/batch3")
async def batch3_status(s: Session = Depends(db),
                        redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Grid v2, volatility-targeted holding and BTC/ETH pairs: the committed pre-registered
    result, and the PAPER forward test of the variant that passed (nothing is executed)."""
    from datetime import UTC
    from datetime import datetime as _dt
    from pathlib import Path

    import nexus_backtest
    from nexus_backtest.voltarget_paper import RUN, STATUS_KEY, summary

    def load() -> dict[str, Any]:
        f = Path(nexus_backtest.__file__).with_name("results") / "batch3_2026-11.json"
        bt = json.loads(f.read_text()) if f.exists() else None
        rows = s.scalars(select(models.ForwardTestEvent).where(
            models.ForwardTestEvent.run == RUN).order_by(models.ForwardTestEvent.at)).all()
        days = [r.data for r in rows if r.kind == "day"]
        div = sum(1 for r in rows if r.kind == "divergence")
        gaps = [r.key for r in rows if r.kind == "gap"]
        return {"backtest": bt, "forward": summary(days, div, gaps, _dt.now(UTC))}

    out = await asyncio.to_thread(load)
    raw = await redis.get(STATUS_KEY)
    out["forward"]["status"] = json.loads(raw) if raw else None
    out["preregistration"] = "docs/research/2026-11-26-batch3-preregistration.md"
    return out


@router.get("/research/market-making")
async def market_making_research() -> dict[str, Any]:
    """Market making (two-sided quoting): the committed pre-registered backtest result."""
    from pathlib import Path

    import nexus_backtest

    def load() -> Any:
        f = Path(nexus_backtest.__file__).with_name("results") / "market_making_2026-11.json"
        return json.loads(f.read_text()) if f.exists() else None

    return {"backtest": await asyncio.to_thread(load),
            "preregistration": "docs/research/2026-11-26-market-making-preregistration.md"}


@router.get("/research/statarb-ml")
async def statarb_ml_research() -> dict[str, Any]:
    """Market-neutral statistical model ("Medallion-style"): the committed result."""
    from pathlib import Path

    import nexus_backtest

    def load() -> Any:
        f = Path(nexus_backtest.__file__).with_name("results") / "statarb_ml_2026-11.json"
        return json.loads(f.read_text()) if f.exists() else None

    return {"backtest": await asyncio.to_thread(load),
            "preregistration": "docs/research/2026-11-26-statarb-ml-preregistration.md"}


@router.get("/trading-bot")
async def trading_bot_status(s: Session = Depends(db), redis: Any = Depends(redis_dep),
                             settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """NEXUS bot (EMA/RSI/MACD 5m): mode, live evaluation per pair, recorded signals, open
    and closed positions, P&L and limits. Balances are SIMULATED in paper mode; no real
    order is ever sent by the bot."""
    from dataclasses import asdict as _asdict

    from nexus_execution.bot import BOT_RISK, PORTFOLIO, STATUS_KEY
    from nexus_strategies.bot_strategy import CODE, BotParams

    raw = await redis.get(STATUS_KEY)
    out: dict[str, Any] = {
        "mode": settings.bot_mode, "live_trading": "DISABLED", "simulated_balance": True,
        "pairs": list(settings.trading_pairs), "status": json.loads(raw) if raw else None,
        "strategy": {"code": CODE, "params": _asdict(BotParams()),
                     "owner_approval": ((s.scalar(select(models.Strategy.parameters).where(
                         models.Strategy.code == CODE)) or {}).get("owner_override")),
                     "acknowledgement": OWNER_APPROVAL_ACK},
        "risk": {k: (str(v) if not isinstance(v, (int, float)) else v)
                 for k, v in _asdict(BOT_RISK).items()}}
    sig = s.execute(select(models.TradingSignal, models.TradingPair.symbol)
                    .join(models.Strategy, models.Strategy.id == models.TradingSignal.strategy_id)
                    .join(models.TradingPair, models.TradingPair.id == models.TradingSignal.pair_id)
                    .where(models.Strategy.code == CODE)
                    .order_by(desc(models.TradingSignal.id)).limit(100)).all()
    out["signals"] = [{"time": ser(x.generated_at), "bar_time": ser(x.bar_time), "symbol": sym,
                       "entry": ser(x.entry_price), "stop": ser(x.stop_loss),
                       "target": ser(x.take_profit), "status": x.status,
                       "reason": x.rejection_reason, "checks": (x.details or {}).get("checks")}
                      for x, sym in sig]
    p = s.scalar(select(models.Portfolio).where(models.Portfolio.name == PORTFOLIO,
                                                models.Portfolio.mode == "paper"))
    if p is None:
        return out
    rows = s.execute(select(models.Position, models.TradingPair.symbol)
                     .join(models.TradingPair, models.TradingPair.id == models.Position.pair_id)
                     .where(models.Position.portfolio_id == p.id)
                     .order_by(desc(models.Position.opened_at)).limit(200)).all()
    pnl = [float(x.realized_pnl) for x, _ in rows if x.status == "closed"]
    gl = -sum(v for v in pnl if v <= 0)
    fees = float(s.scalar(select(func.coalesce(func.sum(models.Order.fee_amount), 0))
                          .where(models.Order.portfolio_id == p.id)) or 0)
    out["portfolio"] = {"id": p.id, "started_at": ser(p.created_at),
                        "initial_capital": ser(p.initial_capital)}
    out["positions"] = [{"symbol": sym, "status": x.status, "quantity": ser(x.quantity),
                         "entry": ser(x.avg_entry_price), "stop": ser(x.stop_loss),
                         "target": ser(x.take_profit), "opened_at": ser(x.opened_at),
                         "closed_at": ser(x.closed_at), "exit_reason": x.exit_reason,
                         "realized_pnl": ser(x.realized_pnl)} for x, sym in rows]
    out["performance"] = {"closed_trades": len(pnl), "net_pnl": sum(pnl),
                          "win_rate": sum(1 for v in pnl if v > 0) / len(pnl) if pnl else None,
                          "profit_factor": sum(v for v in pnl if v > 0) / gl if gl > 0 else None,
                          "fees": fees}
    orders = s.execute(select(models.Order, models.TradingPair.symbol)
                       .join(models.TradingPair, models.TradingPair.id == models.Order.pair_id)
                       .where(models.Order.portfolio_id == p.id)
                       .order_by(desc(models.Order.id)).limit(50)).all()
    out["orders"] = [{"id": o.id, "time": ser(o.created_at), "symbol": sym, "side": o.side,
                      "purpose": o.purpose, "status": o.status, "price": ser(o.price),
                      "amount": ser(o.amount), "filled": ser(o.filled_amount),
                      "fee": ser(o.fee_amount),
                      "reason": (o.details or {}).get("exit_reason")} for o, sym in orders]
    return out


@router.get("/research/library")
async def library_forward_status(s: Session = Depends(db),
                                 redis: Any = Depends(redis_dep)) -> dict[str, Any]:
    """Global strategy library: pre-registered backtest verdicts and the 180-day forward
    paper test (SIMULATED: no orders of any kind are placed)."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nexus_backtest.library_forward import BACKTEST, RUN, STATUS_KEY, summary
    from nexus_strategies.library_global import LIBRARY

    rows = s.scalars(select(models.ForwardTestEvent).where(models.ForwardTestEvent.run == RUN)
                     .order_by(models.ForwardTestEvent.at)).all()
    raw = await redis.get(STATUS_KEY)
    return {"live_trading": "DISABLED", "simulated": True,
            "strategies": {c: {"family": v.family, "source": v.source}
                           for c, v in LIBRARY.items()},
            "backtest": BACKTEST, "forward": summary(list(rows), _dt.now(UTC)),
            "status": json.loads(raw) if raw else None,
            "preregistration": "docs/research/2026-11-23-global-library-preregistration.md"}


BINANCE_PINGS = {
    "spot (live)": "https://api.binance.com/api/v3/ping",
    "futures (live)": "https://fapi.binance.com/fapi/v1/ping",
    "futures (testnet)": "https://testnet.binancefuture.com/fapi/v1/ping",
    "spot public data (mirror)": "https://data-api.binance.vision/api/v3/ping",
}


@router.get("/system/binance-reachability", dependencies=[Depends(owner)])
async def binance_reachability() -> dict[str, Any]:
    """Unauthenticated pings from THIS server. HTTP 451 means Binance refuses the server's
    location; NEXUS reports it and never works around it."""
    import httpx

    out: dict[str, Any] = {}
    from nexus_common.proxy import httpx_kwargs

    async with httpx.AsyncClient(timeout=10, trust_env=True, **httpx_kwargs()) as client:
        for name, url in BINANCE_PINGS.items():
            try:
                r = await client.get(url)
                out[name] = {"status": r.status_code, "reachable": r.status_code == 200,
                             "blocked_by_location": r.status_code == 451}
            except httpx.HTTPError as exc:
                out[name] = {"status": None, "reachable": False, "error": type(exc).__name__}
    return {"checked_at": ser(utcnow()), "hosts": out}
