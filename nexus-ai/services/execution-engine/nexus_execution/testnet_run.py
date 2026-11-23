"""Binance Spot TESTNET round trip: end-to-end proof of connectivity and execution with test
funds (testnet.binance.vision). Never touches a production account.

1. Clock and connectivity (exchange time vs. server time).
2. Exchange information: market rules for the symbol (tick, step, minimum notional).
3. Balances of the testnet account.
4. Market data: the order book, and closed 1h candles (testnet market data, not simulated).
5. Indicators and the trend signal: EMA 50/200, RSI 14, ADX 14, ATR 14, 14-bar momentum.
   * LONG only if close > EMA200, EMA50 > EMA200, ADX >= 20, RSI 45-70 and momentum > 0;
     otherwise NO_TRADE.
6. Risk authorisation: size from 0.25% of the testnet USDT equity with a 1.5 ATR stop,
   fees and slippage included, capped by exposure and cash; rounded to Binance's rules.
   A size the risk engine rejects is NOT sent.
7. Execution: a marketable LIMIT IOC buy, then a LIMIT IOC sell of the filled quantity
   (flat again).
   * Each order has a deterministic client order ID derived from the run ID.
   * Before submitting, the exchange is asked whether that ID already exists: a restarted
     run never places a second order.
   * After a timeout the order is looked up by client ID, never resubmitted.
8. Reconciliation of every order from Binance itself: status, filled quantity, average
   price, fee.
9. Restart drill: the buy is "placed" again with the same run ID and must be found, not
   resubmitted.

This is a CONNECTIVITY test. The buy runs whatever the signal says, but only at a size the
risk engine approved. The signal is recorded next to it.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
from nexus_common.decimals import ZERO, floor_to_step
from nexus_common.timeutil import utcnow
from nexus_config import Timeframe
from nexus_exchange.errors import ExchangeError, OrderStateUnknown
from nexus_exchange.models import OrderRequest, OrderSnapshot, OrderType, Side, TimeInForce
from nexus_risk.sizing import SizingInput, size_position
from nexus_strategies import indicators as ind
from nexus_strategies.frames import to_frame

RISK_PER_TRADE = Decimal("0.0025")
MAX_EXPOSURE = Decimal("0.30")
FEE_RATE = Decimal("0.001")
SLIPPAGE = Decimal("0.0005")
LIMIT_SLIP = Decimal("0.002")          # IOC limit beyond the touch (marketable)


def trend_signal(candles: pd.DataFrame) -> dict[str, Any]:
    """Phase 1 trend-following rule on closed bars; every input is returned."""
    c = candles["close"]
    if len(c) < 210:
        return {"decision": "NO_TRADE", "reason": f"only {len(c)} closed bars (need 210)"}
    e50, e200 = ind.ema(c, 50).iloc[-1], ind.ema(c, 200).iloc[-1]
    rsi, adx = ind.rsi(c, 14).iloc[-1], ind.adx(candles, 14).iloc[-1]
    atr = ind.atr(candles, 14).iloc[-1]
    mom = c.iloc[-1] / c.iloc[-15] - 1
    checks = {"close_above_ema200": bool(c.iloc[-1] > e200), "ema50_above_ema200": bool(e50 > e200),
              "adx_ge_20": bool(adx >= 20), "rsi_45_70": bool(45 <= rsi <= 70),
              "momentum_positive": bool(mom > 0)}
    return {"decision": "LONG" if all(checks.values()) else "NO_TRADE", "checks": checks,
            "close": float(c.iloc[-1]), "ema50": float(e50), "ema200": float(e200),
            "rsi": float(rsi), "adx": float(adx), "atr": float(atr), "momentum_14": float(mom),
            "last_bar": str(candles.index[-1])}


def client_id(run_id: str, leg: str) -> str:
    """Deterministic UUIDv4 for (run, leg): a restarted run reuses the same ID, so the
    order is found on Binance instead of being placed twice."""
    raw = bytearray(hashlib.sha256(f"nexus-testnet:{run_id}:{leg}".encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40          # version 4
    raw[8] = (raw[8] & 0x3F) | 0x80          # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(raw)))


def snap(o: OrderSnapshot) -> dict[str, Any]:
    return {"exchange_order_id": o.exchange_order_id, "client_order_id": o.client_order_id,
            "side": o.side.value, "status": o.status.value, "raw_status": o.raw_status,
            "price": str(o.price) if o.price is not None else None, "amount": str(o.amount),
            "filled": str(o.filled),
            "average_price": str(o.average_price) if o.average_price is not None else None,
            "fee": str(o.fee_cost) if o.fee_cost is not None else None, "fee_asset": o.fee_asset}


async def ensure_order(adapter: Any, req: OrderRequest) -> tuple[OrderSnapshot | None, bool]:
    """Place ``req`` once. If an order with its client ID already exists on Binance it is
    returned and NOT resubmitted. After an uncertain submit it is looked up by client ID.
    Returns (snapshot, newly_placed)."""
    existing = await adapter.find_order_by_client_id(req.client_order_id, req.symbol)
    if existing is not None:
        return existing, False
    try:
        return await adapter.submit_order(req), True
    except OrderStateUnknown:
        return await adapter.find_order_by_client_id(req.client_order_id, req.symbol), True


async def round_trip(adapter: Any, *, symbol: str = "BTC/USDT", run_id: str | None = None
                     ) -> dict[str, Any]:
    run_id = run_id or uuid.uuid4().hex[:12]
    ev: dict[str, Any] = {"run_id": run_id, "environment": "binance spot TESTNET",
                          "symbol": symbol, "started_at": utcnow().isoformat(), "steps": {}}
    steps = ev["steps"]
    try:
        t0 = time.perf_counter()
        health = await adapter.health_check()
        steps["connectivity"] = {"ok": bool(health.ok), "latency_ms": round(
            (time.perf_counter() - t0) * 1000, 1), "detail": health.detail}
        rules = await adapter.market_rules(symbol)
        steps["exchange_info"] = {"price_tick": str(rules.price_tick),
                                  "amount_step": str(rules.amount_step),
                                  "min_amount": str(rules.min_amount),
                                  "min_notional": str(rules.min_notional)}
        bal = await adapter.fetch_balances()
        base, quote = symbol.split("/")
        steps["balances_before"] = {a: {"free": str(b.free), "total": str(b.total)}
                                    for a, b in bal.items() if a in (base, quote)}
        book = await adapter.fetch_order_book(symbol, depth=5)
        steps["order_book"] = {"best_bid": str(book.best_bid), "best_ask": str(book.best_ask),
                               "timestamp": book.timestamp.isoformat()}
        candles = to_frame(await adapter.fetch_closed_candles(
            symbol, Timeframe.H1, since=utcnow() - timedelta(hours=320), limit=320))
        steps["candles"] = {"timeframe": "1h", "closed_bars": len(candles),
                            "first": str(candles.index[0]) if len(candles) else None,
                            "last": str(candles.index[-1]) if len(candles) else None}
        sig = trend_signal(candles)
        steps["signal"] = sig
        # Risk authorisation (independent of the signal).
        cash = bal.get(quote).free if bal.get(quote) else ZERO
        if book.best_ask is None or book.best_bid is None or cash <= 0 or "atr" not in sig:
            steps["risk"] = {"approved": False, "reason": "no book, no testnet USDT or no ATR"}
            ev["result"] = "STOPPED_BEFORE_ORDER"
            return ev
        entry = book.best_ask
        stop = entry - Decimal(str(round(1.5 * sig["atr"], 8)))
        sizing = size_position(SizingInput(
            equity=cash, cash_available=cash, entry_price=entry, stop_price=stop,
            risk_per_trade=RISK_PER_TRADE, fee_rate=FEE_RATE, slippage_rate=SLIPPAGE,
            exposure_now=ZERO, max_exposure=MAX_EXPOSURE, asset_exposure_now=ZERO,
            max_asset_concentration=MAX_EXPOSURE, amount_step=rules.amount_step,
            min_amount=rules.min_amount, min_notional=rules.min_notional))
        steps["risk"] = {"approved": sizing.approved, "quantity": str(sizing.quantity),
                         "notional": str(sizing.notional), "risk_amount": str(sizing.risk_amount),
                         "stop": str(stop), "binding": sizing.binding_constraint,
                         "reason": sizing.rejected_reason,
                         "budget": str(cash * RISK_PER_TRADE)}
        if not sizing.approved:
            ev["result"] = "REJECTED_BY_RISK"
            return ev
        buy_req = await adapter.prepare_order(OrderRequest(
            client_order_id=client_id(run_id, "buy"), symbol=symbol, side=Side.BUY,
            amount=sizing.quantity, price=floor_to_step(entry * (1 + LIMIT_SLIP),
                                                        rules.price_tick),
            order_type=OrderType.LIMIT, time_in_force=TimeInForce.IOC))
        buy, placed = await ensure_order(adapter, buy_req)
        if buy is None:
            ev["result"] = "BUY_STATE_UNKNOWN"
            return ev
        buy = await adapter.find_order_by_client_id(buy_req.client_order_id, symbol) or buy
        steps["buy"] = {**snap(buy), "newly_placed": placed}
        filled = buy.filled
        if filled > 0:
            qty = floor_to_step(filled, rules.amount_step)
            sell_req = await adapter.prepare_order(OrderRequest(
                client_order_id=client_id(run_id, "sell"), symbol=symbol, side=Side.SELL,
                amount=qty, price=floor_to_step(book.best_bid * (1 - LIMIT_SLIP),
                                                rules.price_tick),
                order_type=OrderType.LIMIT, time_in_force=TimeInForce.IOC))
            sell, placed_s = await ensure_order(adapter, sell_req)
            if sell is not None:
                sell = await adapter.find_order_by_client_id(sell_req.client_order_id,
                                                             symbol) or sell
                steps["sell"] = {**snap(sell), "newly_placed": placed_s}
        # Restart drill: same run, same client ID -> found on Binance, not resubmitted.
        again, placed_again = await ensure_order(adapter, buy_req)
        steps["restart_drill"] = {"client_order_id": buy_req.client_order_id,
                                  "found_existing": again is not None and not placed_again,
                                  "resubmitted": placed_again}
        bal2 = await adapter.fetch_balances()
        steps["balances_after"] = {a: {"free": str(b.free), "total": str(b.total)}
                                   for a, b in bal2.items() if a in (base, quote)}
        ok_fill = filled > 0 and "sell" in steps and Decimal(steps["sell"]["filled"]) > 0
        ev["result"] = ("ROUND_TRIP_FILLED" if ok_fill else
                        "BUY_NOT_FILLED" if filled == 0 else "SELL_NOT_FILLED")
        ev["duplicate_protection"] = "passed" if not placed_again else "FAILED"
        return ev
    except ExchangeError as exc:
        ev["result"] = "EXCHANGE_ERROR"
        ev["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return ev
    finally:
        ev["finished_at"] = utcnow().isoformat()
