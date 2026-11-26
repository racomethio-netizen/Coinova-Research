"""Adaptive grid engine (paper): spot long grids and USD-M futures long / short / neutral
grids. Pure and deterministic: no I/O, Decimal money. The paper worker feeds it order-book
snapshots; the backtester feeds it historical bars.

Design (``design``), from the live market:
* **Spacing.** The step is ``spacing_atr`` x hourly ATR%, but never below
  ``min_step_fee_mult`` x a round trip's maker fees: a step that does not pay its fees is
  refused, not traded.
* **Boundaries.** Mid ± ``range_atr`` x ATR, pulled in to recent support and resistance
  when those are closer.
* **Density.** Geometric levels; when the range holds more than ``max_levels`` steps the
  step widens (fewer, wider levels) rather than crowding the book. Fewer than
  ``min_levels`` levels: refused.
* **Size.** Equal quote per slot (no martingale, no size increase). The size is capped by
  the inventory limit and by the per-order risk cap: the loss if that slot is filled and
  the grid is then stopped must be at most ``max_order_risk`` x total paper equity.
* **Stop.** ``stop_atr`` ATRs beyond the range: everything is closed at the book (taker
  fee plus slippage) and the grid stays STOPPED until the owner restarts it.
* **Worst case.** Every slot filled, then the stop. It must not exceed
  ``max_grid_loss`` x allocation, or the grid is refused.

Slots (``Slot``): level i holds one entry and one exit.
* A long slot buys at level i and sells at i + 1; a short slot (futures only) sells at
  level i and buys back at i - 1.
* An entry is "armed" (a resting simulated order) only on the passive side of the book,
  within the inventory and margin limits, and only after the risk hook approves it.
* Partial fills are tracked: the exit quantity follows what was actually filled.
* A slot re-arms only when its round trip is complete.

Fill model (``step``) on one order-book snapshot (bid, ask and their quantities):
* A resting buy at P fills when the ask trades THROUGH P (ask < P). A resting sell fills
  when bid > P.
* At most ``participation`` x the displayed quantity at the touch fills per snapshot.
* Grid fills are maker fills at P; stops and closes are taker fills at the book, with
  slippage.

Futures accounting:
* One-way net position at 1x leverage, isolated margin: margin = |position| x entry /
  leverage, plus the initial margin of armed entries.
* Funding: ``apply_funding(rate, mark)`` charges net position x mark x rate (a long pays a
  positive rate).
* The liquidation price uses Binance's isolated one-way formula with a maintenance-margin
  rate parameter (default 1%, deliberately above Binance's 0.4% first tier for majors).
* A liquidation price within ``min_liq_distance`` of the mark triggers an emergency close.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Any

D = Decimal
ZERO = D(0)


def q(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step if step > 0 else x


@dataclass(frozen=True)
class GridParams:
    spacing_atr: Decimal = Decimal("0.6")
    range_atr: Decimal = Decimal("8")
    min_step_fee_mult: Decimal = Decimal("3")
    min_levels: int = 4
    max_levels: int = 24
    stop_atr: Decimal = Decimal("3")
    max_inventory_frac: Decimal = Decimal("0.6")       # spot: of allocation held in base
    max_margin_util: Decimal = Decimal("0.6")          # futures: of allocation used as margin
    max_grid_loss: Decimal = Decimal("0.12")           # worst case / allocation
    max_order_risk: Decimal = Decimal("0.0025")        # per slot, of TOTAL paper equity
    participation: Decimal = Decimal("0.25")
    leverage: Decimal = Decimal("1")
    mmr: Decimal = Decimal("0.01")
    min_liq_distance: Decimal = Decimal("0.25")
    rebuild_cooldown_s: int = 6 * 3600
    out_of_range_s: int = 4 * 3600


SPOT_FEES = {"maker": D("0.001"), "taker": D("0.001"), "slippage": D("0.0005")}
FUTURES_FEES = {"maker": D("0.0002"), "taker": D("0.0005"), "slippage": D("0.0005")}


@dataclass(frozen=True)
class Filters:
    price_tick: Decimal
    amount_step: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass
class Slot:
    idx: int
    side: str                     # "long" or "short"
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal                  # full slot size (base)
    remaining: Decimal = ZERO     # armed entry quantity still resting
    held: Decimal = ZERO          # filled entry quantity awaiting exit
    held_notional: Decimal = ZERO
    armed: bool = False
    trips: int = 0


@dataclass
class GridState:
    grid_id: str
    market: str                   # "spot" or "futures"
    symbol: str
    mode: str                     # "long", "short", "neutral"
    allocation: Decimal
    levels: list[Decimal]
    anchor: Decimal
    stop_low: Decimal | None
    stop_high: Decimal | None
    fees: dict[str, Decimal]
    params: GridParams
    filters: Filters
    built_at: float
    slots: list[Slot] = field(default_factory=list)
    status: str = "RUNNING"       # RUNNING, PAUSED, SUSPENDED, STOPPED
    quote: Decimal = ZERO         # spot: quote balance incl. reserved; futures: wallet
    realized: Decimal = ZERO
    fees_paid: Decimal = ZERO
    funding_paid: Decimal = ZERO
    round_trips: int = 0
    out_of_range_since: float | None = None
    stop_reason: str | None = None
    fills: int = 0

    # ----------------------------------------------------------------- positions
    @property
    def long_qty(self) -> Decimal:
        return sum((s.held for s in self.slots if s.side == "long"), ZERO)

    @property
    def short_qty(self) -> Decimal:
        return sum((s.held for s in self.slots if s.side == "short"), ZERO)

    @property
    def net_qty(self) -> Decimal:
        return self.long_qty - self.short_qty

    def entry_avg(self) -> Decimal | None:
        lots = [s for s in self.slots if s.held > 0]
        qty = sum((s.held for s in lots), ZERO)
        return sum((s.held_notional for s in lots), ZERO) / qty if qty else None

    def reserved(self) -> Decimal:
        """Quote reserved by armed entries (spot: notional + fee; futures: margin)."""
        f = self.fees["maker"]
        if self.market == "spot":
            return sum((s.remaining * s.entry_price * (1 + f) for s in self.slots
                        if s.armed and s.side == "long"), ZERO)
        return sum((s.remaining * s.entry_price for s in self.slots if s.armed), ZERO) \
            / self.params.leverage

    def position_margin(self) -> Decimal:
        return sum((s.held_notional for s in self.slots), ZERO) / self.params.leverage

    def unrealized(self, bid: Decimal, ask: Decimal) -> Decimal:
        u = ZERO
        for s in self.slots:
            if s.held > 0:
                u += (s.held * bid - s.held_notional) if s.side == "long" \
                    else (s.held_notional - s.held * ask)
        return u

    def equity(self, bid: Decimal, ask: Decimal) -> Decimal:
        if self.market == "spot":
            return self.quote + self.long_qty * bid
        return self.quote + self.unrealized(bid, ask)

    def liquidation_price(self) -> Decimal | None:
        """Binance isolated one-way: LP = (WB - side x Q x EP) / (Q x MMR - side x Q), with
        WB the isolated wallet (position margin). None when flat or spot."""
        if self.market != "futures":
            return None
        net = self.net_qty
        if net == 0:
            return None
        side = D(1) if net > 0 else D(-1)
        qty = abs(net)
        ep = self.entry_avg() or ZERO
        wb = qty * ep / self.params.leverage
        denom = qty * self.params.mmr - side * qty
        lp = (wb - side * qty * ep) / denom if denom != 0 else ZERO
        return max(lp, ZERO)

    def open_orders(self) -> list[dict[str, Any]]:
        out = []
        for s in self.slots:
            if s.armed and s.remaining > 0:
                out.append({"slot": s.idx, "kind": "entry", "side": "buy" if s.side == "long"
                            else "sell", "price": str(s.entry_price), "qty": str(s.remaining)})
            if s.held > 0:
                out.append({"slot": s.idx, "kind": "exit", "side": "sell" if s.side == "long"
                            else "buy", "price": str(s.exit_price), "qty": str(s.held)})
        return out

    def to_json(self) -> dict[str, Any]:
        def c(v: Any) -> Any:
            return str(v) if isinstance(v, Decimal) else v
        return {"grid_id": self.grid_id, "market": self.market, "symbol": self.symbol,
                "mode": self.mode, "allocation": c(self.allocation),
                "levels": [c(x) for x in self.levels], "anchor": c(self.anchor),
                "stop_low": c(self.stop_low), "stop_high": c(self.stop_high),
                "fees": {k: c(v) for k, v in self.fees.items()},
                "params": {k: c(v) for k, v in asdict(self.params).items()},
                "filters": {k: c(v) for k, v in asdict(self.filters).items()},
                "built_at": self.built_at,
                "slots": [{k: c(v) for k, v in asdict(s).items()} for s in self.slots],
                "status": self.status, "quote": c(self.quote), "realized": c(self.realized),
                "fees_paid": c(self.fees_paid), "funding_paid": c(self.funding_paid),
                "round_trips": self.round_trips, "out_of_range_since": self.out_of_range_since,
                "stop_reason": self.stop_reason, "fills": self.fills}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GridState:
        def dd(v: Any) -> Decimal | None:
            return None if v is None else D(str(v))
        pk: dict[str, Any] = {
            k: (D(v) if isinstance(GridParams.__dataclass_fields__[k].default, Decimal) else v)
            for k, v in d["params"].items()}
        p = GridParams(**pk)
        st = cls(d["grid_id"], d["market"], d["symbol"], d["mode"], D(d["allocation"]),
                 [D(x) for x in d["levels"]], D(d["anchor"]), dd(d["stop_low"]),
                 dd(d["stop_high"]), {k: D(v) for k, v in d["fees"].items()}, p,
                 Filters(**{k: D(v) for k, v in d["filters"].items()}), d["built_at"])
        for s in d["slots"]:
            st.slots.append(Slot(s["idx"], s["side"], D(s["entry_price"]), D(s["exit_price"]),
                                 D(s["qty"]), D(s["remaining"]), D(s["held"]),
                                 D(s["held_notional"]), s["armed"], s["trips"]))
        st.status, st.quote, st.realized = d["status"], D(d["quote"]), D(d["realized"])
        st.fees_paid, st.funding_paid = D(d["fees_paid"]), D(d["funding_paid"])
        st.round_trips, st.out_of_range_since = d["round_trips"], d["out_of_range_since"]
        st.stop_reason, st.fills = d["stop_reason"], d["fills"]
        return st


# --------------------------------------------------------------------------- design
@dataclass(frozen=True)
class DesignInput:
    market: str
    symbol: str
    mode: str
    mid: Decimal
    atr_pct: Decimal                 # hourly ATR / close
    support: Decimal | None
    resistance: Decimal | None
    allocation: Decimal
    total_equity: Decimal
    filters: Filters
    now: float


def geometric_levels(lower: Decimal, upper: Decimal, step: Decimal) -> list[Decimal]:
    out, p = [], lower
    while p <= upper * (1 + step / 1000):
        out.append(p)
        p = p * (1 + step)
    return out


def design(inp: DesignInput, params: GridParams | None = None,
           grid_id: str | None = None) -> tuple[GridState | None, dict[str, Any]]:
    """The grid for the current market, or (None, report with the reasons)."""
    p = params or GridParams()
    fees = SPOT_FEES if inp.market == "spot" else FUTURES_FEES
    report: dict[str, Any] = {"reasons": []}
    if inp.market == "spot" and inp.mode != "long":
        report["reasons"].append("spot grids are long-only (no short selling)")
        return None, report
    floor = p.min_step_fee_mult * 2 * fees["maker"]
    step = max(p.spacing_atr * inp.atr_pct, floor)
    half = p.range_atr * inp.atr_pct * inp.mid
    lower, upper = inp.mid - half, inp.mid + half
    if inp.support is not None and inp.mid * (1 - 2 * step) > inp.support > lower:
        lower = inp.support * (1 - step / 2)
    if inp.resistance is not None and inp.mid * (1 + 2 * step) < inp.resistance < upper:
        upper = inp.resistance * (1 + step / 2)
    n = int((upper / lower).ln() / (1 + step).ln()) + 1 if upper > lower > 0 else 0
    if n > p.max_levels:
        step = ((upper / lower).ln() / D(p.max_levels - 1)).exp() - 1
        n = p.max_levels
    report.update({"step_pct": str(step), "fee_floor": str(floor), "lower": str(lower),
                   "upper": str(upper), "levels": n})
    if n < p.min_levels:
        report["reasons"].append(f"only {n} levels fit at a fee-covering step of "
                                 f"{step:.3%}: range too narrow")
        return None, report
    levels = [q(x, inp.filters.price_tick) for x in geometric_levels(lower, upper, step)]
    if len(set(levels)) != len(levels):
        report["reasons"].append("levels collapse at this price tick")
        return None, report
    stop_low = lower * (1 - p.stop_atr * inp.atr_pct) if inp.mode in ("long", "neutral") \
        else None
    stop_high = upper * (1 + p.stop_atr * inp.atr_pct) if inp.mode in ("short", "neutral") \
        else None
    sides = []
    for i in range(len(levels)):
        if inp.mode in ("long", "neutral") and i < len(levels) - 1:
            sides.append(("long", i, levels[i], levels[i + 1]))
        if inp.mode in ("short", "neutral") and i > 0:
            sides.append(("short", i, levels[i], levels[i - 1]))
    longs = sum(1 for s in sides if s[0] == "long")
    shorts = sum(1 for s in sides if s[0] == "short")
    cap_frac = p.max_inventory_frac if inp.market == "spot" else p.max_margin_util
    per = inp.allocation * cap_frac * p.leverage / max(longs, shorts, 1)
    # Per-order risk: slot filled, then stopped (taker exit with slippage).
    worst_frac = ZERO
    for side, _, ep, _ in sides:
        if side == "long" and stop_low is not None:
            lf = 1 - stop_low * (1 - fees["slippage"]) / ep + fees["maker"] + fees["taker"]
        elif side == "short" and stop_high is not None:
            lf = stop_high * (1 + fees["slippage"]) / ep - 1 + fees["maker"] + fees["taker"]
        else:
            continue
        worst_frac = max(worst_frac, lf)
    risk_cap = p.max_order_risk * inp.total_equity / worst_frac if worst_frac > 0 else per
    quote_per_slot = min(per, risk_cap)
    # Smallest exchange-valid slot: the minimum notional at the LOWEST level, rounded UP
    # to the lot step, then priced at the HIGHEST level (the most quote it can need).
    step_qty = inp.filters.amount_step
    qty_min = max((inp.filters.min_notional * D("1.02") / levels[0] / step_qty)
                  .to_integral_value(rounding=ROUND_CEILING) * step_qty, inp.filters.min_qty)
    need = qty_min * levels[-1]
    report.update({"quote_per_slot": str(quote_per_slot), "inventory_cap": str(per),
                   "risk_cap": str(risk_cap), "min_slot_quote": str(need),
                   "binding": "risk" if risk_cap < per else "inventory"})
    if quote_per_slot < need:
        # Too many levels for this allocation: use fewer, wider levels (each slot then
        # meets the exchange minimum) when the inventory limit, not risk, is binding.
        fit = int(inp.allocation * cap_frac * p.leverage / need)
        if per <= risk_cap and fit + 1 >= p.min_levels and fit + 1 < n:
            return design(inp, replace(p, max_levels=fit + 1), grid_id)
        report["reasons"].append(f"slot size {quote_per_slot:.2f} below the exchange "
                                 f"minimum order {inp.filters.min_notional}")
        return None, report
    st = GridState(grid_id or f"{inp.market}-{inp.symbol}-{int(inp.now)}", inp.market,
                   inp.symbol, inp.mode, inp.allocation, levels, inp.mid, stop_low, stop_high,
                   dict(fees), p, inp.filters, inp.now, quote=inp.allocation)
    for side, i, ep, xp in sides:
        qty = max(q(quote_per_slot / ep, step_qty), qty_min)
        st.slots.append(Slot(i, side, ep, xp, qty))
    wc = worst_case_loss(st)
    report["worst_case_loss"] = str(wc)
    if wc > inp.allocation * p.max_grid_loss:
        report["reasons"].append(f"worst case {wc:.2f} > {p.max_grid_loss:.0%} of the "
                                 f"allocation {inp.allocation:.2f}")
        return None, report
    return st, report


def worst_case_loss(st: GridState) -> Decimal:
    """Every slot of the worse side filled, then that side's stop (taker, slippage)."""
    f = st.fees
    sides = {"long": ZERO, "short": ZERO}
    for s in st.slots:
        if s.side == "long" and st.stop_low is not None:
            px = st.stop_low * (1 - f["slippage"])
            sides["long"] += s.qty * (s.entry_price - px) + s.qty * (
                s.entry_price * f["maker"] + px * f["taker"])
        elif s.side == "short" and st.stop_high is not None:
            px = st.stop_high * (1 + f["slippage"])
            sides["short"] += s.qty * (px - s.entry_price) + s.qty * (
                s.entry_price * f["maker"] + px * f["taker"])
    return max(sides.values())


# ---------------------------------------------------------------------------- fills
@dataclass(frozen=True)
class Book:
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal


Authorizer = Callable[[GridState, Slot], tuple[bool, list[str]]]


def _allow(st: GridState, slot: Slot) -> tuple[bool, list[str]]:
    return True, []


def arm(st: GridState, book: Book, authorize: Authorizer = _allow) -> list[dict[str, Any]]:
    """Arm idle slots on the passive side, within inventory / margin limits, each one
    approved by ``authorize`` (the central risk manager). Returns the decisions."""
    if st.status != "RUNNING":
        return []
    p, out = st.params, []
    for s in sorted(st.slots, key=lambda x: -x.entry_price if x.side == "long"
                    else x.entry_price):
        if s.armed or s.held > 0:
            continue
        if s.side == "long" and not (s.entry_price < book.bid and
                                     (st.mode != "neutral" or s.entry_price < st.anchor)):
            continue
        if s.side == "short" and not (s.entry_price > book.ask and
                                      (st.mode != "neutral" or s.entry_price > st.anchor)):
            continue
        need = s.qty * s.entry_price
        if st.market == "spot":
            exposure = st.long_qty * book.bid + st.reserved() / (1 + st.fees["maker"])
            free = st.quote - st.reserved()
            if exposure + need > st.allocation * p.max_inventory_frac or \
                    need * (1 + st.fees["maker"]) > free:
                continue
        else:
            margin = st.position_margin() + st.reserved() + need / p.leverage
            if margin > st.allocation * p.max_margin_util:
                continue
        ok, why = authorize(st, s)
        out.append({"slot": s.idx, "side": s.side, "price": str(s.entry_price),
                    "qty": str(s.qty), "approved": ok, "reasons": why})
        if ok:
            s.armed, s.remaining = True, s.qty
    return out


def disarm(st: GridState) -> int:
    """Cancel all resting entries (exits stay so inventory can still unwind)."""
    n = 0
    for s in st.slots:
        if s.armed and s.remaining > 0:
            n += 1
        s.armed, s.remaining = (False, ZERO) if s.held == 0 else (s.armed, ZERO)
    return n


def _fill(st: GridState, s: Slot, qty: Decimal, price: Decimal, kind: str,
          fee_rate: Decimal, *, trip: bool = True) -> dict[str, Any]:
    fee = qty * price * fee_rate
    st.fees_paid += fee
    st.fills += 1
    pnl: Decimal | None = None
    if kind == "entry":
        s.remaining -= qty
        s.held += qty
        s.held_notional += qty * price
        if st.market == "spot":
            st.quote -= qty * price + fee
        else:
            st.quote -= fee
    else:
        part = s.held_notional * qty / s.held
        pnl = (qty * price - part) if s.side == "long" else (part - qty * price)
        s.held -= qty
        s.held_notional -= part
        st.realized += pnl
        if st.market == "spot":
            st.quote += qty * price - fee
        else:
            st.quote += pnl - fee
        if s.held == 0 and s.remaining == 0:
            s.armed = False
            if trip:                          # a stop-out is not a completed grid trip
                s.trips += 1
                st.round_trips += 1
    side = ("buy" if s.side == "long" else "sell") if kind == "entry" else \
        ("sell" if s.side == "long" else "buy")
    return {"slot": s.idx, "kind": kind, "side": side, "price": str(price), "qty": str(qty),
            "fee": str(fee), "pnl": None if pnl is None else str(pnl)}


def step(st: GridState, book: Book, now: float) -> list[dict[str, Any]]:
    """Advance on one order-book snapshot. Returns the fills (simulated)."""
    if st.status == "STOPPED":
        return []
    if st.stop_low is not None and book.bid <= st.stop_low and st.long_qty > 0:
        return close_all(st, book, f"stop-loss: bid {book.bid} <= {st.stop_low:.8g}")
    if st.stop_high is not None and book.ask >= st.stop_high and st.short_qty > 0:
        return close_all(st, book, f"stop-loss: ask {book.ask} >= {st.stop_high:.8g}")
    lp = st.liquidation_price()
    if lp is not None and lp > 0:
        mark = (book.bid + book.ask) / 2
        if abs(mark - lp) / mark < st.params.min_liq_distance:
            return close_all(st, book, f"liquidation distance {abs(mark - lp) / mark:.1%} < "
                                       f"{st.params.min_liq_distance:.0%}")
    p, fills = st.params, []
    cap_ask, cap_bid = book.ask_qty * p.participation, book.bid_qty * p.participation
    for s in st.slots:
        # Exits first (they reduce risk), then entries.
        if s.held > 0:
            hit = book.bid > s.exit_price if s.side == "long" else book.ask < s.exit_price
            cap = cap_bid if s.side == "long" else cap_ask
            qty = q(min(s.held, cap), st.filters.amount_step)
            if hit and qty > 0:
                fills.append(_fill(st, s, qty, s.exit_price, "exit", st.fees["maker"]))
                if s.side == "long":
                    cap_bid -= qty
                else:
                    cap_ask -= qty
    for s in st.slots:
        if s.armed and s.remaining > 0:
            hit = book.ask < s.entry_price if s.side == "long" else book.bid > s.entry_price
            cap = cap_ask if s.side == "long" else cap_bid
            qty = q(min(s.remaining, cap), st.filters.amount_step)
            if hit and qty > 0:
                fills.append(_fill(st, s, qty, s.entry_price, "entry", st.fees["maker"]))
                if s.side == "long":
                    cap_ask -= qty
                else:
                    cap_bid -= qty
    lo, hi = st.levels[0], st.levels[-1]
    inside = lo <= book.bid <= hi
    st.out_of_range_since = None if inside else (st.out_of_range_since or now)
    return fills


def close_all(st: GridState, book: Book, reason: str, *,
              status: str = "STOPPED") -> list[dict[str, Any]]:
    """Cancel everything and close every lot at the book (taker + slippage)."""
    f, fills = st.fees, []
    for s in st.slots:
        s.armed, s.remaining = False, ZERO
        if s.held > 0:
            px = book.bid * (1 - f["slippage"]) if s.side == "long" \
                else book.ask * (1 + f["slippage"])
            fill = _fill(st, s, s.held, px, "exit", f["taker"], trip=False)
            fill["reason"] = reason
            fills.append(fill)
    st.status, st.stop_reason = status, reason
    return fills


def apply_funding(st: GridState, rate: Decimal, mark: Decimal) -> Decimal:
    """Settle one funding event on the net futures position. Returns the payment
    (positive = paid)."""
    if st.market != "futures" or st.net_qty == 0:
        return ZERO
    pay = st.net_qty * mark * rate
    st.funding_paid += pay
    st.quote -= pay
    return pay


def should_rebuild(st: GridState, now: float, mode_now: str | None) -> str | None:
    """Reason to rebuild (only when flat, after the cool-down), else None."""
    if st.long_qty > 0 or st.short_qty > 0 or st.status == "STOPPED":
        return None
    if now - st.built_at < st.params.rebuild_cooldown_s:
        return None
    if mode_now is not None and mode_now != st.mode:
        return f"regime now favours a {mode_now} grid (was {st.mode})"
    if st.out_of_range_since and now - st.out_of_range_since >= st.params.out_of_range_s:
        return "price outside the grid for longer than the out-of-range limit"
    return None


def summary(st: GridState, book: Book) -> dict[str, Any]:
    eq = st.equity(book.bid, book.ask)
    lp = st.liquidation_price()
    return {"grid_id": st.grid_id, "market": st.market, "symbol": st.symbol,
            "mode": st.mode, "status": st.status, "allocation": str(st.allocation),
            "equity": str(eq), "net_pnl": str(eq - st.allocation),
            "realized": str(st.realized), "unrealized": str(st.unrealized(book.bid, book.ask)),
            "fees_paid": str(st.fees_paid), "funding_paid": str(st.funding_paid),
            "round_trips": st.round_trips, "fills": st.fills,
            "long_qty": str(st.long_qty), "short_qty": str(st.short_qty),
            "reserved": str(st.reserved()), "position_margin": str(st.position_margin()),
            "liquidation_price": None if lp is None else str(lp),
            "lower": str(st.levels[0]), "upper": str(st.levels[-1]),
            "levels": len(st.levels), "stop_low": None if st.stop_low is None
            else str(st.stop_low), "stop_high": None if st.stop_high is None
            else str(st.stop_high), "worst_case_loss": str(worst_case_loss(st)),
            "open_orders": len(st.open_orders()), "stop_reason": st.stop_reason}
