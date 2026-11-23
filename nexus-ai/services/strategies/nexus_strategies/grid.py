"""Spot grid bot (Pionex / 3Commas style), long-only, no leverage. Pure and deterministic.

How it works:
* A price range [lower, upper] is split into ``levels`` prices (geometric spacing by
  default, so each step is the same percentage).
* Below the current price, NEXUS keeps one resting BUY per level. When a buy at level i
  fills, a SELL for that same quantity is placed one level up (i + 1). When that sell
  fills, the buy at level i is placed again. Each completed buy→sell round trip earns one
  grid step minus two maker fees.
* Every level uses the SAME quote amount (capital / levels). There is no martingale and no
  size increase. The total capital is fixed in advance, so the bot can never commit more
  than ``capital``.

Risk controls (owner rules: no uncontrolled averaging down, capital protection first):
* **Worst case known in advance.** ``worst_case_loss`` = the loss if every buy fills and
  the price then reaches the stop. A grid whose worst case exceeds ``max_loss_fraction``
  of its capital is refused.
* **Stop-loss** below the range: every held lot is sold and the grid stops. It does NOT
  restart by itself; the owner restarts it (no automatic recovery trading).
* **Above the range**, no new buys are placed. Remaining sells stay; nothing is chased.
* The step must exceed ``2 x fee`` by a margin, or each round trip would lose money.

Fill model (paper):
* A resting buy at P fills when the best ask trades THROUGH P (ask < P).
* A resting sell fills when the best bid goes above P (bid > P).
* Both fill at P (maker), with the maker fee. Touching P is not enough: the queue position
  is unknown, so this is conservative.
* The stop exit sells at the best bid (taker fee) plus slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from typing import Any

D = Decimal
ZERO = D(0)


@dataclass(frozen=True)
class GridConfig:
    symbol: str
    lower: Decimal
    upper: Decimal
    levels: int = 20                         # number of grid prices (levels - 1 steps)
    capital: Decimal = Decimal("1000")             # quote currency committed to the grid
    geometric: bool = True
    stop_loss_pct: Decimal = Decimal("0.03")       # stop this far below ``lower``
    maker_fee: Decimal = Decimal("0.001")
    taker_fee: Decimal = Decimal("0.001")
    stop_slippage: Decimal = Decimal("0.001")
    amount_step: Decimal = Decimal("0.00001")      # Binance BTC/USDT lot step
    price_tick: Decimal = Decimal("0.01")
    min_notional: Decimal = Decimal("5")
    max_loss_fraction: Decimal = Decimal("0.10")   # worst case <= 10% of grid capital
    min_step_over_fees: Decimal = Decimal("2")     # step >= 2 x (two maker fees)

    @property
    def stop_price(self) -> Decimal:
        return self.lower * (1 - self.stop_loss_pct)


def _q(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def build_levels(cfg: GridConfig) -> list[Decimal]:
    if cfg.levels < 3 or not cfg.upper > cfg.lower > 0:
        raise ValueError("need at least 3 levels and upper > lower > 0")
    n = cfg.levels - 1
    if cfg.geometric:
        ratio = (cfg.upper / cfg.lower) ** (D(1) / D(n))
        raw = [cfg.lower * ratio ** i for i in range(n + 1)]
    else:
        step = (cfg.upper - cfg.lower) / n
        raw = [cfg.lower + step * i for i in range(n + 1)]
    out = [_q(p, cfg.price_tick) for p in raw]
    out[-1] = cfg.upper                       # the range ends exactly at ``upper``
    return out


def step_pct(levels: list[Decimal]) -> Decimal:
    return min((levels[i + 1] / levels[i] - 1) for i in range(len(levels) - 1))


def quote_per_level(cfg: GridConfig) -> Decimal:
    return cfg.capital / D(cfg.levels - 1)    # the top level only ever sells


def worst_case_loss(cfg: GridConfig, levels: list[Decimal]) -> Decimal:
    """Loss if EVERY buy level fills and the price then falls to the stop (taker exit
    with slippage). Grid profits already banked are ignored (conservative)."""
    per = quote_per_level(cfg)
    exit_px = cfg.stop_price * (1 - cfg.stop_slippage)
    loss = ZERO
    for p in levels[:-1]:
        qty = per / p
        cost = per * (1 + cfg.maker_fee)
        proceeds = qty * exit_px * (1 - cfg.taker_fee)
        loss += cost - proceeds
    return loss


def validate(cfg: GridConfig) -> list[str]:
    """Reasons to refuse this grid (empty = acceptable)."""
    try:
        levels = build_levels(cfg)
    except ValueError as exc:
        return [str(exc)]
    out: list[str] = []
    st = step_pct(levels)
    need = 2 * cfg.maker_fee * cfg.min_step_over_fees
    if st < need:
        out.append(f"grid step {st:.4%} < {need:.4%} (2 maker fees x "
                   f"{cfg.min_step_over_fees}): round trips would not pay their fees")
    per = quote_per_level(cfg)
    if per < cfg.min_notional:
        out.append(f"{per:.2f} per level < Binance minimum order {cfg.min_notional}")
    if len(set(levels)) != len(levels):
        out.append("levels collapse at this price tick: range too narrow")
    wc = worst_case_loss(cfg, levels)
    if wc > cfg.capital * cfg.max_loss_fraction:
        out.append(f"worst-case loss {wc:.2f} > {cfg.max_loss_fraction:.0%} of capital "
                   f"({cfg.capital * cfg.max_loss_fraction:.2f}); narrow the range or "
                   "tighten the stop")
    return out


def auto_range(highs: list[Decimal], lows: list[Decimal], last: Decimal, *,
               pad: Decimal = Decimal("0.01"), min_width: Decimal = Decimal("0.04"),
               max_width: Decimal = Decimal("0.15")) -> tuple[Decimal, Decimal]:
    """Range from recent closed-bar highs and lows (e.g. the last 7 days of 1h candles),
    padded, then widened or narrowed around ``last`` to stay within [min, max] width."""
    lo, hi = min(lows) * (1 - pad), max(highs) * (1 + pad)
    width = hi / lo - 1
    if width < min_width or width > max_width:
        w = min(max(width, min_width), max_width)
        half = (1 + w).sqrt()
        lo, hi = last / half, last * half
    lo, hi = min(lo, last * D("0.999")), max(hi, last * D("1.001"))
    return lo, hi


# ----------------------------------------------------------------------------- state
@dataclass(frozen=True)
class Order:
    side: str             # "buy" or "sell"
    level: int            # index into levels
    price: Decimal
    qty: Decimal


@dataclass
class GridState:
    cfg: GridConfig
    levels: list[Decimal]
    status: str = "RUNNING"                  # RUNNING, ABOVE_RANGE, STOPPED
    quote: Decimal = ZERO                    # free quote not reserved by buy orders
    base: Decimal = ZERO                     # held base (reserved by sell orders)
    orders: dict[tuple[str, int], Order] = field(default_factory=dict)
    realized: Decimal = ZERO                 # grid profit net of fees (completed trips)
    fees: Decimal = ZERO
    round_trips: int = 0
    buys: int = 0
    sells: int = 0
    lots: dict[int, tuple[Decimal, Decimal]] = field(default_factory=dict)  # level -> (qty, cost)
    stop_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"symbol": self.cfg.symbol, "status": self.status,
                "lower": str(self.cfg.lower), "upper": str(self.cfg.upper),
                "stop_price": str(self.cfg.stop_price), "capital": str(self.cfg.capital),
                "levels": [str(p) for p in self.levels],
                "quote": str(self.quote), "base": str(self.base),
                "orders": [{"side": o.side, "level": o.level, "price": str(o.price),
                            "qty": str(o.qty)} for o in sorted(
                                self.orders.values(), key=lambda o: -o.price)],
                "realized": str(self.realized), "fees": str(self.fees),
                "round_trips": self.round_trips, "buys": self.buys, "sells": self.sells,
                "lots": {str(k): [str(q), str(c)] for k, (q, c) in self.lots.items()},
                "stop_reason": self.stop_reason}

    @classmethod
    def from_json(cls, cfg: GridConfig, d: dict[str, Any]) -> GridState:
        st = cls(cfg, [D(p) for p in d["levels"]], d["status"], D(d["quote"]), D(d["base"]))
        for o in d["orders"]:
            st.orders[(o["side"], o["level"])] = Order(o["side"], o["level"], D(o["price"]),
                                                       D(o["qty"]))
        st.realized, st.fees = D(d["realized"]), D(d["fees"])
        st.round_trips, st.buys, st.sells = d["round_trips"], d["buys"], d["sells"]
        st.lots = {int(k): (D(q), D(c)) for k, (q, c) in d["lots"].items()}
        st.stop_reason = d.get("stop_reason")
        return st


def start(cfg: GridConfig, price: Decimal) -> GridState:
    """Fresh grid: a buy at every level strictly below ``price``. No initial market buy:
    the grid only holds coins after the price has come down to it."""
    problems = validate(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    levels = build_levels(cfg)
    st = GridState(cfg, levels, quote=cfg.capital)
    per = quote_per_level(cfg)
    for i, p in enumerate(levels[:-1]):
        if p < price:
            _place_buy(st, i, per)
    if price >= cfg.upper:
        st.status = "ABOVE_RANGE"
    return st


def _place_buy(st: GridState, i: int, quote_amt: Decimal) -> None:
    p = st.levels[i]
    qty = _q(quote_amt / p, st.cfg.amount_step)
    reserve = qty * p * (1 + st.cfg.maker_fee)
    if qty <= 0 or reserve > st.quote:
        return
    st.quote -= reserve
    st.orders[("buy", i)] = Order("buy", i, p, qty)


@dataclass(frozen=True)
class Fill:
    side: str
    level: int
    price: Decimal
    qty: Decimal
    fee: Decimal
    profit: Decimal | None = None             # set on a sell: net of both fees
    reason: str = "grid"


def step(st: GridState, best_bid: Decimal, best_ask: Decimal) -> list[Fill]:
    """Advance the grid on one book observation. Mutates ``st``; returns the fills."""
    if st.status == "STOPPED":
        return []
    cfg, fills = st.cfg, []
    if best_bid <= cfg.stop_price:
        return _stop(st, best_bid, f"stop-loss: bid {best_bid} <= {cfg.stop_price:.2f}")
    per = quote_per_level(cfg)
    # Buys fill when the ask trades through the level (highest first: the first reached).
    for key in sorted([k for k in st.orders if k[0] == "buy"], key=lambda k: -k[1]):
        o = st.orders[key]
        if best_ask < o.price:
            del st.orders[key]
            fee = o.qty * o.price * cfg.maker_fee
            st.quote += o.qty * o.price * cfg.maker_fee - fee   # release the reserved fee
            st.base += o.qty
            st.fees += fee
            st.buys += 1
            st.lots[o.level] = (o.qty, o.qty * o.price + fee)
            fills.append(Fill("buy", o.level, o.price, o.qty, fee))
            up = o.level + 1
            st.orders[("sell", up)] = Order("sell", up, st.levels[up], o.qty)
    for key in sorted([k for k in st.orders if k[0] == "sell"], key=lambda k: k[1]):
        o = st.orders[key]
        if best_bid > o.price:
            del st.orders[key]
            gross = o.qty * o.price
            fee = gross * cfg.maker_fee
            st.base -= o.qty
            st.quote += gross - fee
            st.fees += fee
            st.sells += 1
            _, lot_cost = st.lots.pop(o.level - 1, (o.qty, gross))
            profit = gross - fee - lot_cost
            st.realized += profit
            st.round_trips += 1
            fills.append(Fill("sell", o.level, o.price, o.qty, fee, profit))
            below = o.level - 1
            if st.status == "RUNNING" and ("buy", below) not in st.orders:
                _place_buy(st, below, per)
    # Above the range: no NEW buys (existing ones stay), nothing is chased.
    if best_bid >= cfg.upper and st.status == "RUNNING":
        st.status = "ABOVE_RANGE"
    elif best_ask < cfg.upper and st.status == "ABOVE_RANGE":
        st.status = "RUNNING"
    return fills


def _stop(st: GridState, bid: Decimal, reason: str) -> list[Fill]:
    cfg, fills = st.cfg, []
    for key in [k for k in st.orders if k[0] == "buy"]:
        o = st.orders.pop(key)
        st.quote += o.qty * o.price * (1 + cfg.maker_fee)          # release reservation
    for key in [k for k in st.orders if k[0] == "sell"]:
        st.orders.pop(key)
    if st.base > 0:
        px = bid * (1 - cfg.stop_slippage)
        gross = st.base * px
        fee = gross * cfg.taker_fee
        cost = sum((c for _, c in st.lots.values()), ZERO)
        profit = gross - fee - cost
        fills.append(Fill("sell", -1, px, st.base, fee, profit, "stop-loss"))
        st.quote += gross - fee
        st.realized += profit
        st.fees += fee
        st.sells += 1
        st.base = ZERO
        st.lots.clear()
    st.status, st.stop_reason = "STOPPED", reason
    return fills


def mark(st: GridState, best_bid: Decimal) -> dict[str, Any]:
    """Equity and P&L at the current bid (the price the held coins could be sold at)."""
    reserved = sum((o.qty * o.price * (1 + st.cfg.maker_fee)
                    for o in st.orders.values() if o.side == "buy"), ZERO)
    held_value = st.base * best_bid
    cost = sum((c for _, c in st.lots.values()), ZERO)
    equity = st.quote + reserved + held_value
    return {"equity": str(equity), "pnl": str(equity - st.cfg.capital),
            "pnl_pct": str((equity / st.cfg.capital - 1) if st.cfg.capital else ZERO),
            "realized": str(st.realized), "unrealized": str(held_value - cost),
            "held_base": str(st.base), "held_value": str(held_value),
            "open_buys": sum(1 for o in st.orders.values() if o.side == "buy"),
            "open_sells": sum(1 for o in st.orders.values() if o.side == "sell")}


def with_capital(cfg: GridConfig, capital: Decimal) -> GridConfig:
    return replace(cfg, capital=capital)
