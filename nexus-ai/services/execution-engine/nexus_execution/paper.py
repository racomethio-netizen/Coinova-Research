"""Paper execution simulator: fills against REAL order book snapshots.

* A marketable limit order walks the visible book up to its limit price (taker fee,
  plus configured extra slippage on each level as a conservative penalty). Unfilled
  remainder rests (GTC) or is cancelled (IOC).
* A resting limit order fills (maker fee, at its limit price) only when the opposite
  side of a LATER book has crossed it, so the order was genuinely marketable.
* Every simulated fill is labeled with a ``paper-`` trade id. Paper fills are never
  presented as exchange executions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from nexus_common.decimals import ZERO
from nexus_exchange.models import OrderBook, OrderRequest, Side, TimeInForce


@dataclass(frozen=True)
class SimFill:
    trade_id: str
    price: Decimal
    amount: Decimal
    fee: Decimal
    is_maker: bool


@dataclass(frozen=True)
class SimResult:
    fills: tuple[SimFill, ...]
    remaining: Decimal
    resting: bool                   # remainder stays open on the simulated book


def _trade_id() -> str:
    return f"paper-{uuid.uuid4()}"


def simulate_submit(req: OrderRequest, book: OrderBook, *, taker_fee: Decimal,
                    extra_slippage: Decimal) -> SimResult:
    if req.price is None:
        raise ValueError("paper broker accepts limit orders only")
    levels = book.asks if req.side is Side.BUY else book.bids
    remaining = req.amount
    fills: list[SimFill] = []
    for price, qty in levels:
        crosses = price <= req.price if req.side is Side.BUY else price >= req.price
        if not crosses or remaining <= 0:
            break
        take = min(qty, remaining)
        px = price * (1 + extra_slippage) if req.side is Side.BUY else price * (1 - extra_slippage)
        # Never fill worse than the order's own limit.
        px = min(px, req.price) if req.side is Side.BUY else max(px, req.price)
        fills.append(SimFill(_trade_id(), px, take, px * take * taker_fee, False))
        remaining -= take
    resting = remaining > 0 and req.time_in_force is TimeInForce.GTC
    return SimResult(tuple(fills), remaining, resting)


def simulate_resting(side: Side, limit: Decimal, remaining: Decimal, book: OrderBook, *,
                     maker_fee: Decimal) -> SimResult:
    """Fill a resting order if the latest book crossed its price (conservatively only
    up to the size visible at or through the limit)."""
    if remaining <= 0:
        return SimResult((), ZERO, False)
    if side is Side.BUY:
        available = sum((q for p, q in book.asks if p <= limit), ZERO)
    else:
        available = sum((q for p, q in book.bids if p >= limit), ZERO)
    take = min(available, remaining)
    if take <= 0:
        return SimResult((), remaining, True)
    fill = SimFill(_trade_id(), limit, take, limit * take * maker_fee, True)
    return SimResult((fill,), remaining - take, remaining - take > 0)
