"""Pre-trade liquidity and execution planning from the live order book (Hummingbot-style
connector data, institutional "implementation shortfall" thinking).

* ``estimate``: walk the book for a quantity; returns the spread, the expected average
  fill, the slippage against the mid and against the touch, the depth inside a price
  band, and whether the visible book can fill the order at all.
* ``plan``: chooses how to execute:
  * ``post_only``: a maker limit at the touch when the spread is wide relative to fees and
    the order is not urgent (exits are always urgent);
  * ``ioc_limit``: a marketable limit capped at the walked price plus a buffer, never an
    unbounded market order;
  * slicing into equal child orders when the order is a large share of the visible depth;
  * refusal (with reasons) when the spread or the expected slippage exceeds the limits,
    or the book cannot fill it even when sliced.

Only visible depth is known; hidden liquidity and queue position are not modelled. The
estimate is therefore conservative for marketable orders. A maker order may not fill at
all.

The checks apply to ENTRIES. An exit is never refused for liquidity: refusing an exit can
only increase risk. Instead it is planned as an IOC (sliced if needed), with the warnings
recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from nexus_common.decimals import ZERO, floor_to_step
from nexus_exchange.models import OrderBook, Side

BPS = Decimal("10000")


@dataclass(frozen=True)
class LiquidityEstimate:
    side: str
    quantity: Decimal
    mid: Decimal | None
    touch: Decimal | None
    spread_bps: Decimal | None
    avg_price: Decimal | None
    filled: Decimal
    fully_fillable: bool
    levels_used: int
    slippage_vs_mid_bps: Decimal | None
    slippage_vs_touch_bps: Decimal | None
    worst_price: Decimal | None
    depth_in_band: Decimal            # base quantity available within ``band_bps`` of mid

    def to_json(self) -> dict[str, Any]:
        def f(x: Decimal | None) -> str | None:
            return None if x is None else str(x)
        return {"side": self.side, "quantity": str(self.quantity), "mid": f(self.mid),
                "touch": f(self.touch), "spread_bps": f(self.spread_bps),
                "avg_price": f(self.avg_price), "filled": str(self.filled),
                "fully_fillable": self.fully_fillable, "levels_used": self.levels_used,
                "slippage_vs_mid_bps": f(self.slippage_vs_mid_bps),
                "slippage_vs_touch_bps": f(self.slippage_vs_touch_bps),
                "worst_price": f(self.worst_price), "depth_in_band": str(self.depth_in_band)}


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"))


def estimate(book: OrderBook, side: Side, quantity: Decimal, *,
             band_bps: Decimal = Decimal("50")) -> LiquidityEstimate:
    """Walk the opposite side of ``book`` for ``quantity`` (a BUY consumes asks)."""
    levels = book.asks if side is Side.BUY else book.bids
    mid = book.mid
    touch = levels[0][0] if levels else None
    spread = (_q((book.best_ask - book.best_bid) / mid * BPS)
              if mid and book.best_ask is not None and book.best_bid is not None else None)
    remaining, cost, used, worst = quantity, ZERO, 0, None
    for price, amount in levels:
        if remaining <= 0:
            break
        take = min(remaining, amount)
        cost += take * price
        remaining -= take
        used += 1
        worst = price
    filled = quantity - remaining
    avg = cost / filled if filled > 0 else None
    sign = Decimal(1) if side is Side.BUY else Decimal(-1)
    slip_mid = _q(sign * (avg - mid) / mid * BPS) if avg is not None and mid else None
    slip_touch = (_q(sign * (avg - touch) / touch * BPS)
                  if avg is not None and touch else None)
    depth = ZERO
    if mid:
        lim = mid * (1 + sign * band_bps / BPS)
        depth = sum((a for p, a in levels if (p <= lim if side is Side.BUY else p >= lim)),
                    ZERO)
    return LiquidityEstimate(side.value, quantity, mid, touch, spread, avg, filled,
                             remaining <= 0, used, slip_mid, slip_touch, worst, depth)


@dataclass(frozen=True)
class ExecutionLimits:
    max_spread_bps: Decimal = Decimal("25")
    max_slippage_bps: Decimal = Decimal("30")       # expected, vs mid, for the whole order
    max_participation: Decimal = Decimal("0.25")    # of the depth within the band per child
    max_slices: int = 5
    band_bps: Decimal = Decimal("50")
    post_only_min_spread_bps: Decimal = Decimal("8")  # wide enough to be worth waiting for
    ioc_buffer_bps: Decimal = Decimal("10")         # beyond the walked worst price


@dataclass(frozen=True)
class ExecutionPlan:
    action: str                     # post_only, ioc_limit, REJECT
    limit_price: Decimal | None
    child_quantities: tuple[Decimal, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    estimate: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.action != "REJECT"

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "approved": self.approved,
                "limit_price": None if self.limit_price is None else str(self.limit_price),
                "child_quantities": [str(q) for q in self.child_quantities],
                "reasons": list(self.reasons), "warnings": list(self.warnings),
                "estimate": self.estimate}


def _round_price(p: Decimal, tick: Decimal | None, side: Side) -> Decimal:
    if not tick or tick <= 0:
        return p
    down = floor_to_step(p, tick)
    if side is Side.SELL:
        return down
    return down if down == p else down + tick      # a buy cap rounds UP (stays marketable)


def plan(book: OrderBook, side: Side, quantity: Decimal, *, purpose: str = "entry",
         urgent: bool = False, supports_post_only: bool = True,
         price_tick: Decimal | None = None, amount_step: Decimal | None = None,
         limits: ExecutionLimits | None = None) -> ExecutionPlan:
    lim = limits or ExecutionLimits()
    est = estimate(book, side, quantity, band_bps=lim.band_bps)
    ej = est.to_json()
    is_exit = purpose != "entry"
    problems: list[str] = []
    if est.mid is None or est.touch is None:
        problems.append("order book empty or one-sided")
    if est.spread_bps is not None and est.spread_bps > lim.max_spread_bps:
        problems.append(f"spread {est.spread_bps} bps > {lim.max_spread_bps} bps")
    if not est.fully_fillable:
        problems.append(f"visible book fills only {est.filled} of {quantity}")
    if est.slippage_vs_mid_bps is not None and est.slippage_vs_mid_bps > lim.max_slippage_bps:
        problems.append(f"expected slippage {est.slippage_vs_mid_bps} bps > "
                        f"{lim.max_slippage_bps} bps")
    if problems and not is_exit:
        return ExecutionPlan("REJECT", None, (), tuple(problems), (), ej)
    warnings = tuple(problems) if is_exit else ()
    if est.touch is None:                       # an exit with no book: nothing to plan on
        return ExecutionPlan("REJECT", None, (), ("no order book to price the exit",),
                             warnings, ej)
    # Slicing: each child at most max_participation of the depth inside the band.
    per_child = est.depth_in_band * lim.max_participation
    n = 1
    if per_child > 0 and quantity > per_child:
        n = int((quantity / per_child).to_integral_value(rounding="ROUND_CEILING"))
    if n > lim.max_slices and not is_exit:
        return ExecutionPlan("REJECT", None, (), (
            f"order needs {n} slices of <= {lim.max_participation:.0%} of the depth within "
            f"{lim.band_bps} bps (max {lim.max_slices}): too large for this book",), (), ej)
    n = max(1, min(n, lim.max_slices))
    child = quantity / n
    if amount_step:
        child = floor_to_step(child, amount_step)
    children = [child] * (n - 1) + [quantity - child * (n - 1)]
    wide = est.spread_bps is not None and est.spread_bps >= lim.post_only_min_spread_bps
    if supports_post_only and wide and not urgent and not is_exit:
        # Join the passive side of the book: never crosses, a maker fill or nothing.
        passive = book.best_bid if side is Side.BUY else book.best_ask
        assert passive is not None
        return ExecutionPlan("post_only", _round_price(passive, price_tick, side),
                             tuple(children), (
                                 f"spread {est.spread_bps} bps >= "
                                 f"{lim.post_only_min_spread_bps} bps and not urgent: rest "
                                 "as maker (may not fill)",), warnings, ej)
    worst = est.worst_price or est.touch
    sign = Decimal(1) if side is Side.BUY else Decimal(-1)
    cap = worst * (1 + sign * lim.ioc_buffer_bps / BPS)
    why = (f"marketable IOC capped {lim.ioc_buffer_bps} bps beyond the walked worst price "
           f"{worst}; expected slippage {est.slippage_vs_mid_bps} bps vs mid")
    if n > 1:
        why += f"; {n} slices of <= {lim.max_participation:.0%} of depth in band"
    return ExecutionPlan("ioc_limit", _round_price(cap, price_tick, side), tuple(children),
                         (why,), warnings, ej)
