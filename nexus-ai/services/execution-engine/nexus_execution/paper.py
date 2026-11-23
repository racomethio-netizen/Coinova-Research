"""Paper execution simulator: fills against REAL order book snapshots.

* A marketable limit order walks the visible book up to its limit price (taker fee,
  plus configured extra slippage on each level as a conservative penalty). Unfilled
  remainder rests (GTC) or is cancelled (IOC).
* A resting limit order fills (maker fee, at its limit price) only when the opposite
  side of a LATER book has crossed it, so the order was genuinely marketable.
* Every simulated fill is labeled with a ``paper-`` trade id. Paper fills are never
  presented as exchange executions.
* Realism (PaperBroker): a latency delay before filling against a FRESH book (price can
  move away: worse fill or a missed IOC), a participation cap per level (partial fills),
  stale books refused (treated as an outage), and optional injected outages, both
  "refused" and "sent but no reply" (the latter must be reconciled by client order ID).
* Binance order rules are mirrored, so paper cannot do what the exchange would refuse:
  a post-only order that would take liquidity is refused (Binance LIMIT_MAKER), and a
  stop-limit order that would trigger immediately is refused (STOP_LOSS_LIMIT).
* Stop-limit: rests untriggered; once the market reaches the stop it becomes a limit order
  at its limit price, taking whatever the book offers up to that limit (taker), with the
  rest resting (maker). Binance triggers on the LAST TRADE price; paper uses the touch
  (best bid for a sell stop, best ask for a buy stop), the closest live proxy it has.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from nexus_common.decimals import ZERO
from nexus_exchange.errors import InvalidOrder
from nexus_exchange.models import OrderBook, OrderRequest, OrderType, Side, TimeInForce


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
    triggered: bool | None = None   # stop-limit only: has the stop been reached


def _trade_id() -> str:
    return f"paper-{uuid.uuid4()}"


def would_trigger(side: Side, stop: Decimal, book: OrderBook) -> bool:
    """Has the market reached a stop? (sell stop: bid at or below it; buy stop: ask at or
    above it)."""
    if side is Side.SELL:
        return book.best_bid is not None and book.best_bid <= stop
    return book.best_ask is not None and book.best_ask >= stop


def _take(side: Side, limit: Decimal, amount: Decimal, book: OrderBook, *,
          taker_fee: Decimal, extra_slippage: Decimal,
          participation: Decimal) -> tuple[list[SimFill], Decimal]:
    """Walk the visible book up to ``limit`` (taker fills)."""
    levels = book.asks if side is Side.BUY else book.bids
    remaining = amount
    fills: list[SimFill] = []
    for price, qty in levels:
        crosses = price <= limit if side is Side.BUY else price >= limit
        if not crosses or remaining <= 0:
            break
        take = min(qty * participation, remaining)
        if take <= 0:
            continue
        px = price * (1 + extra_slippage) if side is Side.BUY else price * (1 - extra_slippage)
        # Never fill worse than the order's own limit.
        px = min(px, limit) if side is Side.BUY else max(px, limit)
        fills.append(SimFill(_trade_id(), px, take, px * take * taker_fee, False))
        remaining -= take
    return fills, remaining


def simulate_submit(req: OrderRequest, book: OrderBook, *, taker_fee: Decimal,
                    extra_slippage: Decimal, participation: Decimal = Decimal(1)) -> SimResult:
    """``participation`` = share of each visible level this order may take (other traders
    compete for the same liquidity); below 1 it produces realistic partial fills.

    Raises InvalidOrder where Binance would refuse the order."""
    if req.price is None:
        raise ValueError("paper broker accepts limit and stop-limit orders only")
    if req.post_only:
        touch = book.best_ask if req.side is Side.BUY else book.best_bid
        if touch is not None and (touch <= req.price if req.side is Side.BUY
                                  else touch >= req.price):
            raise InvalidOrder("post-only order would immediately match and take; refused "
                               "(as Binance refuses a LIMIT_MAKER order)")
    if req.order_type is OrderType.STOP_LIMIT:
        assert req.stop_price is not None
        if would_trigger(req.side, req.stop_price, book):
            raise InvalidOrder("stop-limit order would trigger immediately; refused "
                               "(as Binance refuses such a STOP_LOSS_LIMIT order)")
        return SimResult((), req.amount, True, triggered=False)
    fills, remaining = _take(req.side, req.price, req.amount, book, taker_fee=taker_fee,
                             extra_slippage=extra_slippage, participation=participation)
    resting = remaining > 0 and req.time_in_force is TimeInForce.GTC
    return SimResult(tuple(fills), remaining, resting)


def simulate_stop(side: Side, stop: Decimal, limit: Decimal, remaining: Decimal,
                  book: OrderBook, *, taker_fee: Decimal, extra_slippage: Decimal,
                  participation: Decimal = Decimal(1)) -> SimResult:
    """An untriggered stop-limit order against a newer book: nothing until the stop is
    reached, then a GTC limit order at ``limit`` (taking what it can, the rest rests)."""
    if not would_trigger(side, stop, book):
        return SimResult((), remaining, True, triggered=False)
    fills, left = _take(side, limit, remaining, book, taker_fee=taker_fee,
                        extra_slippage=extra_slippage, participation=participation)
    return SimResult(tuple(fills), left, left > 0, triggered=True)


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
