"""Cross-market portfolio risk book (unified brief, section F): one independent approval
for every proposed trade, forex and crypto, over all NEXUS accounts together.

The book is every account NEXUS trades in paper, each with its equity, its day and week
starting equity, its peak, its open positions (side, size, entry, stop, price) and
whether it is reconciled with its broker:
* the crypto paper book;
* the Exness DEMO account.

USD and USDT are treated as one currency.

Checks (every one is reported; any failure rejects):
* **Portfolio:**
  * the kill switch;
  * daily loss ≥ 1%, weekly loss ≥ 2.5%, drawdown ≥ 5% (the drawdown also asks the
    caller to PAUSE trading via the kill switch);
  * every account reconciled and fresh;
  * at most 3 open positions in all.
* **Trade:**
  * no second position in the same instrument;
  * the planned reward/risk is at least 2:1 (central, whatever the strategy);
  * size from 0.25% of the executing account's equity at risk to the stop. The risk
    includes a 10% allowance for spread and slippage, and is converted to USD (pairs
    quoted in another currency, such as USDJPY, are divided by the price);
  * all open risk plus this trade is at most 1% of total equity;
  * correlation: positions that move with this trade (return correlation ≥ 0.7 in the
    same direction, or, when unknown, the same currency exposure on the same side)
    count as one cluster, which may risk at most 0.5% of total equity;
  * no leverage: total open notional at most 1x total equity, and spot crypto at most
    30% of its account. The size is reduced to fit, or the trade is rejected.
* **Allocation proposals** (a portfolio rule such as the crypto swing trend):
  * no stop, so the 2:1 and per-trade sizing do not apply;
  * the portfolio checks and the exposure caps do;
  * a REDUCTION is always approved, because cutting exposure cannot add risk.

Pure and Decimal-only: the caller builds the book and decides what to do with the
answer. Nothing here places an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from nexus_config.settings import RiskSettings

ZERO = Decimal(0)
SLIPPAGE_ALLOWANCE = Decimal("1.10")
USD_LIKE = ("USD", "USDT", "USDC")


def legs(symbol: str) -> tuple[str, str] | None:
    """(base, quote) with USD-like quotes as USD: EURUSD, XAUUSD, BTC/USDT, USDJPY."""
    s = symbol.upper()
    if "/" in s:
        b, q = s.split("/", 1)
    elif len(s) == 6:
        b, q = s[:3], s[3:]
    else:
        return None
    return b, ("USD" if q in USD_LIKE else q)


def usd_per_unit(symbol: str, price: Decimal) -> tuple[Decimal | None, Decimal | None]:
    """(USD value of one unit, USD value of a one-price-unit move per unit). None when
    the instrument is neither quoted in USD nor has USD as its base (a cross)."""
    lg = legs(symbol)
    if lg is None or price <= 0:
        return None, None
    base, quote = lg
    if quote == "USD":
        return price, Decimal(1)
    if base == "USD":
        return Decimal(1), Decimal(1) / price
    return None, None


@dataclass(frozen=True)
class Position:
    account: str
    market: str
    symbol: str
    side: int                     # +1 long, -1 short
    quantity: Decimal             # units of the base (not lots)
    entry: Decimal
    stop: Decimal | None
    price: Decimal                # latest price

    def notional_usd(self) -> Decimal:
        unit, _ = usd_per_unit(self.symbol, self.price)
        return self.quantity * unit if unit is not None else ZERO

    def risk_usd(self) -> Decimal:
        """Money lost if the stop is hit; without a stop, the whole notional."""
        _, move = usd_per_unit(self.symbol, self.entry)
        if self.stop is None or move is None:
            return self.notional_usd()
        return max(ZERO, self.side * (self.entry - self.stop)) * self.quantity * move

    def unrealized_usd(self) -> Decimal | None:
        """Open profit (+) or loss (-) in USD at the latest price; None for a cross."""
        _, move = usd_per_unit(self.symbol, self.price)
        if move is None:
            return None
        return self.side * (self.price - self.entry) * self.quantity * move

    def exposures(self) -> dict[str, Decimal]:
        return exposures(self.symbol, self.side, self.notional_usd())


def exposures(symbol: str, side: int, notional_usd: Decimal) -> dict[str, Decimal]:
    lg = legs(symbol)
    if lg is None:
        return {}
    base, quote = lg
    return {base: side * notional_usd, quote: -side * notional_usd}


@dataclass(frozen=True)
class Account:
    name: str
    market: str
    equity: Decimal
    day_start: Decimal
    week_start: Decimal
    peak: Decimal
    positions: tuple[Position, ...]
    reconciled: bool
    note: str = ""


@dataclass(frozen=True)
class Book:
    accounts: tuple[Account, ...]
    as_of: datetime

    def total(self, attr: str) -> Decimal:
        return sum((getattr(a, attr) for a in self.accounts), ZERO)

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(p for a in self.accounts for p in a.positions)

    def account(self, name: str) -> Account | None:
        return next((a for a in self.accounts if a.name == name), None)


@dataclass(frozen=True)
class Limits:
    risk_per_trade: Decimal
    daily_loss: Decimal
    weekly_loss: Decimal
    drawdown: Decimal
    max_positions: int
    max_open_risk: Decimal
    max_spot_crypto_exposure: Decimal
    correlation_threshold: float
    min_reward_risk: Decimal = Decimal(2)
    max_cluster_risk: Decimal = Decimal("0.005")    # two trades' worth
    max_gross_leverage: Decimal = Decimal(1)

    @classmethod
    def from_settings(cls, r: RiskSettings) -> Limits:
        return cls(r.risk_per_trade, r.daily_loss_limit, r.weekly_loss_limit, r.max_drawdown,
                   r.max_open_positions, r.max_open_risk, r.max_portfolio_exposure,
                   r.correlation_threshold, max_cluster_risk=2 * r.risk_per_trade)


@dataclass(frozen=True)
class Proposal:
    market: str
    symbol: str
    side: int
    module: str
    account: str
    kind: str = "bracket"                 # "bracket" or "allocation"
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    notional_usd: Decimal | None = None   # allocation: the change in USD (positive)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Approval:
    approved: bool
    quantity: Decimal | None = None
    risk_usd: Decimal | None = None
    notional_usd: Decimal | None = None
    pause: bool = False                   # drawdown breach: engage the kill switch
    checks: list[Check] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [c.detail for c in self.checks if not c.passed]

    def as_dict(self) -> dict[str, Any]:
        return {"approved": self.approved,
                "quantity": str(self.quantity) if self.quantity is not None else None,
                "risk_usd": str(self.risk_usd) if self.risk_usd is not None else None,
                "notional_usd": str(self.notional_usd) if self.notional_usd is not None
                else None, "pause": self.pause, "reasons": self.reasons,
                "checks": [c.__dict__ for c in self.checks]}


def _loss(start: Decimal, now: Decimal) -> Decimal:
    return (start - now) / start if start > 0 else ZERO


def portfolio_checks(book: Book, lim: Limits, *, kill_switch: bool) -> tuple[list[Check], bool]:
    eq = book.total("equity")
    day, week = _loss(book.total("day_start"), eq), _loss(book.total("week_start"), eq)
    dd = _loss(book.total("peak"), eq)
    stale = [a.name for a in book.accounts if not a.reconciled]
    n = len(book.positions)
    checks = [
        Check("kill_switch", not kill_switch, "kill switch engaged" if kill_switch
              else "kill switch clear"),
        Check("daily_loss", day < lim.daily_loss,
              f"daily loss {day:.2%} (limit {lim.daily_loss:.2%})"),
        Check("weekly_loss", week < lim.weekly_loss,
              f"weekly loss {week:.2%} (limit {lim.weekly_loss:.2%})"),
        Check("drawdown", dd < lim.drawdown,
              f"drawdown {dd:.2%} from the peak (pause at {lim.drawdown:.2%})"),
        Check("reconciled", not stale and bool(book.accounts),
              "no account in the book" if not book.accounts else
              (f"not reconciled or stale: {', '.join(stale)}" if stale
               else "all accounts reconciled")),
        Check("open_positions", n < lim.max_positions,
              f"{n} open positions (maximum {lim.max_positions})"),
    ]
    return checks, dd >= lim.drawdown


def approve(p: Proposal, book: Book, lim: Limits, *, kill_switch: bool,
            correlations: dict[frozenset[str], float] | None = None) -> Approval:
    checks, pause = portfolio_checks(book, lim, kill_switch=kill_switch)
    total_eq = book.total("equity")
    acct = book.account(p.account)
    if acct is None:
        checks.append(Check("account", False, f"no {p.account} account connected"))
        return Approval(False, pause=pause, checks=checks)
    held = [q for q in book.positions if q.symbol == p.symbol]
    if p.kind == "allocation":
        return _allocation(p, book, acct, lim, checks, pause, held)
    checks.append(Check("no_duplicate", not held,
                        f"already a position in {p.symbol}" if held else "no position yet"))
    if p.entry is None or p.stop is None or p.target is None or p.entry == p.stop:
        checks.append(Check("plan", False, "entry, stop and target are required"))
        return Approval(False, pause=pause, checks=checks)
    rr = p.side * (p.target - p.entry) / (p.side * (p.entry - p.stop))
    checks.append(Check("reward_risk", rr >= lim.min_reward_risk,
                        f"planned reward/risk {rr:.2f}:1 (minimum {lim.min_reward_risk}:1)"))
    unit, move = usd_per_unit(p.symbol, p.entry)
    if unit is None or move is None or p.side * (p.entry - p.stop) <= 0:
        checks.append(Check("sizing", False, "cannot size: stop on the wrong side or a "
                                             "cross pair without a USD leg"))
        return Approval(False, pause=pause, checks=checks)
    budget = lim.risk_per_trade * acct.equity
    per_unit = (p.side * (p.entry - p.stop)) * move * SLIPPAGE_ALLOWANCE
    qty = budget / per_unit
    gross_room = lim.max_gross_leverage * total_eq - sum(
        (q.notional_usd() for q in book.positions), ZERO)
    caps = [gross_room / unit]
    if p.market == "crypto":
        crypto_held = sum((q.notional_usd() for q in acct.positions), ZERO)
        caps.append((lim.max_spot_crypto_exposure * acct.equity - crypto_held) / unit)
    qty = min([qty, *caps])
    qty = max(ZERO, qty).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    risk = qty * per_unit
    checks.append(Check("size", qty > 0 and risk >= budget * Decimal("0.25"),
                        f"size {qty} units, {risk:.2f} USD at risk (budget {budget:.2f}; "
                        "below a quarter of the budget is too small)"))
    open_risk = sum((q.risk_usd() for q in book.positions), ZERO)
    cap = lim.max_open_risk * total_eq
    checks.append(Check("open_risk", open_risk + risk <= cap,
                        f"open risk {open_risk + risk:.2f} USD with this trade "
                        f"(cap {cap:.2f} = {lim.max_open_risk:.2%})"))
    cluster = _cluster(p, book, correlations or {}, lim)
    cluster_risk = sum((q.risk_usd() for q in cluster), ZERO) + risk
    ccap = lim.max_cluster_risk * total_eq
    checks.append(Check("correlation", cluster_risk <= ccap,
                        f"correlated cluster {', '.join(q.symbol for q in cluster) or 'none'}"
                        f": {cluster_risk:.2f} USD at risk (cap {ccap:.2f})"))
    ok = all(c.passed for c in checks)
    return Approval(ok, qty if ok else None, risk if ok else None,
                    qty * unit if ok else None, pause, checks)


def _cluster(p: Proposal, book: Book, corr: dict[frozenset[str], float],
             lim: Limits) -> list[Position]:
    mine = exposures(p.symbol, p.side, Decimal(1))
    out = []
    for q in book.positions:
        rho = corr.get(frozenset({p.symbol, q.symbol}))
        if rho is not None:
            together = rho * p.side * q.side >= lim.correlation_threshold
        else:   # unknown: the same currency exposed on the same side counts as together
            theirs = q.exposures()
            together = any(theirs.get(c, ZERO) != 0 and v != 0
                           and (v > 0) == (theirs[c] > 0) for c, v in mine.items())
        if together:
            out.append(q)
    return out


def _allocation(p: Proposal, book: Book, acct: Account, lim: Limits, checks: list[Check],
                pause: bool, held: list[Position]) -> Approval:
    change = p.notional_usd or ZERO
    if p.side < 0:      # reducing exposure is always allowed, whatever the limits say
        return Approval(True, None, ZERO, change, pause,
                        [Check("reduction", True, "reducing exposure is always allowed")])
    crypto_held = sum((q.notional_usd() for q in acct.positions), ZERO)
    cap = lim.max_spot_crypto_exposure * acct.equity
    checks.append(Check("exposure", crypto_held + change <= cap,
                        f"spot exposure {crypto_held + change:.2f} USD after this change "
                        f"(cap {cap:.2f} = {lim.max_spot_crypto_exposure:.0%} of the account)"))
    gross = sum((q.notional_usd() for q in book.positions), ZERO) + change
    checks.append(Check("leverage", gross <= lim.max_gross_leverage * book.total("equity"),
                        f"total notional {gross:.2f} USD (no leverage)"))
    ok = all(c.passed for c in checks)
    return Approval(ok, None, None, change if ok else None, pause, checks)
