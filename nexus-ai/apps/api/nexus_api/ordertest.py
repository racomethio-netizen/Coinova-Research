"""Exchange round-trip test for demo/testnet futures keys (never production).

Opens the smallest allowed BTCUSDT long, places an exchange-side stop and take-profit,
verifies each step against what Binance reports, then closes reduce-only and cancels the
protective orders. Cleanup always runs. A step is "ok" only when the exchange confirms it.
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal
from typing import Any

from nexus_common.decimals import floor_to_step
from nexus_exchange.binance_futures import BinanceFuturesAdapter, open_protected_position

SYMBOL = "BTC/USDT:USDT"
TEST_LEVERAGE = 2


def _ceil_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


async def run_round_trip(ad: BinanceFuturesAdapter, symbol: str = SYMBOL) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    alerts: list[str] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    async def alert(msg: str) -> None:
        alerts.append(msg)

    placed: list[str] = []
    qty = Decimal(0)
    try:
        if await ad.hedge_mode_enabled():
            step("position mode", False, "hedge mode is on; switch to one-way in Binance")
            return {"ok": False, "steps": steps, "alerts": alerts}
        step("position mode", True, "one-way")
        existing = [p for p in await ad.positions(symbol) if p.quantity != 0]
        if existing:
            step("no existing position", False, "a BTCUSDT position is already open; not touching it")
            return {"ok": False, "steps": steps, "alerts": alerts}
        await ad.ensure_isolated(symbol)
        step("isolated margin", True)
        await ad.set_leverage(symbol, TEST_LEVERAGE)
        step("leverage", True, f"{TEST_LEVERAGE}x")
        rules = await ad.rules(symbol)
        mark = (await ad.mark(symbol))["mark_price"]
        qty = _ceil_step(max(rules.min_qty, rules.min_notional * Decimal("1.05") / mark),
                         rules.market_step_size)
        stop = floor_to_step(mark * Decimal("0.97"), rules.tick_size)
        target = floor_to_step(mark * Decimal("1.03"), rules.tick_size)
        step("order size", True, f"{qty} BTC (~{(qty * mark).quantize(Decimal('0.01'))} USDT), "
             f"stop {stop}, target {target}")

        res = await open_protected_position(ad, symbol=symbol, side="long", quantity=qty,
                                            stop_price=stop, take_profit_price=target,
                                            alert=alert)
        placed = [x for x in (res.stop_id, res.take_profit_id) if x]
        if not step("entry filled", res.filled_qty > 0,
                    f"filled {res.filled_qty} (exchange-confirmed, order {res.entry_id})"):
            return {"ok": False, "steps": steps, "alerts": alerts}
        qty = res.filled_qty
        pos = [p for p in await ad.positions(symbol) if p.quantity != 0]
        step("position visible at exchange", bool(pos) and pos[0].quantity == qty,
             f"{pos[0].quantity} BTC, entry {pos[0].entry_price}, liquidation "
             f"{pos[0].liquidation_price}" if pos else "no position reported")
        open_ids = {o["client_order_id"]: o for o in await ad.open_orders(symbol)}
        step("stop-loss resting at exchange", res.stop_id in open_ids,
             str(open_ids.get(res.stop_id or "", {}).get("type", "missing")))
        step("take-profit resting at exchange", res.take_profit_id in open_ids,
             str(open_ids.get(res.take_profit_id or "", {}).get("type", "missing")))
    finally:
        # Always leave the account flat with no leftover orders from this test.
        if qty > 0:
            pos = [p for p in await ad.positions(symbol) if p.quantity != 0]
            if pos:
                await ad.submit_reduce_only_market(symbol=symbol, close_side="SELL",
                                                   quantity=abs(pos[0].quantity))
            flat = not [p for p in await ad.positions(symbol) if p.quantity != 0]
            step("position closed (reduce-only)", flat, "flat" if flat else "still open!")
        for cid in placed:
            remaining = {o["client_order_id"] for o in await ad.open_orders(symbol)}
            if cid in remaining:
                await ad.cancel(symbol, cid)
        if placed:
            left = {o["client_order_id"] for o in await ad.open_orders(symbol)} & set(placed)
            step("protective orders cancelled", not left, "none left" if not left else str(left))
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "alerts": alerts}
