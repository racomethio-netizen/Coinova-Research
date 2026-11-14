"""Central multi-exchange portfolio allocator.

Chooses WHERE to buy (and confirms where to sell) given per-venue state. It never
assumes venues are equivalent: each venue must list the pair, be healthy, have fresh
reconciled balances, a fresh quote and enough cash after pending reservations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

MAX_BALANCE_AGE_SECONDS = 300


@dataclass(frozen=True)
class VenueState:
    exchange: str
    lists_pair: bool
    healthy: bool
    quote_free: Decimal              # free quote balance reported/ledgered
    quote_reserved: Decimal          # pending buy orders not yet reflected as locked
    best_ask: Decimal | None
    best_bid: Decimal | None
    ask_depth_quote: Decimal | None  # quote liquidity near the touch
    taker_fee: Decimal
    balances_synced_at: datetime | None
    balance_mismatch: bool           # internal ledger vs exchange disagree

    def cash_available(self) -> Decimal:
        return max(Decimal(0), self.quote_free - self.quote_reserved)


@dataclass(frozen=True)
class Allocation:
    exchange: str | None
    reason: str
    effective_price: Decimal | None = None
    rejected: tuple[tuple[str, str], ...] = ()


def choose_buy_venue(venues: list[VenueState], *, notional: Decimal, now: datetime,
                     max_depth_share: Decimal = Decimal("0.25")) -> Allocation:
    """Cheapest effective price (ask x (1 + fee)) among venues able to fill safely."""
    rejected: list[tuple[str, str]] = []
    eligible: list[tuple[Decimal, VenueState]] = []
    for v in venues:
        why = _ineligible(v, now)
        if why is None and v.best_ask is None:
            why = "no quote"
        if why is None and v.cash_available() < notional * (1 + v.taker_fee):
            why = f"insufficient cash ({v.cash_available()} < {notional})"
        if why is None and (v.ask_depth_quote is None
                            or notional > v.ask_depth_quote * max_depth_share):
            why = "order too large for visible depth"
        if why is not None:
            rejected.append((v.exchange, why))
            continue
        assert v.best_ask is not None
        eligible.append((v.best_ask * (1 + v.taker_fee), v))
    if not eligible:
        return Allocation(None, "no eligible venue", rejected=tuple(rejected))
    price, venue = min(eligible, key=lambda e: (e[0], e[1].exchange))
    return Allocation(venue.exchange, "best effective price", price, tuple(rejected))


def check_sell_venue(venue: VenueState, *, now: datetime) -> Allocation:
    """Exits must happen where the position is held; just verify it is safe."""
    why = _ineligible(venue, now)
    if why is None and venue.best_bid is None:
        why = "no quote"
    if why:
        return Allocation(None, why, rejected=((venue.exchange, why),))
    return Allocation(venue.exchange, "position venue")


def _ineligible(v: VenueState, now: datetime) -> str | None:
    if not v.lists_pair:
        return "pair not listed"
    if not v.healthy:
        return "exchange unhealthy"
    if v.balance_mismatch:
        return "balance mismatch (account state uncertain)"
    if v.balances_synced_at is None or (now - v.balances_synced_at).total_seconds() \
            > MAX_BALANCE_AGE_SECONDS:
        return "balances stale"
    return None


@dataclass(frozen=True)
class BalanceDiff:
    asset: str
    internal: Decimal
    exchange: Decimal

    @property
    def difference(self) -> Decimal:
        return self.exchange - self.internal


def reconcile_balances(internal: dict[str, Decimal], exchange: dict[str, Decimal], *,
                       tolerance: dict[str, Decimal] | None = None,
                       default_tolerance: Decimal = Decimal("0.00000001")) -> list[BalanceDiff]:
    """Totals that differ by more than tolerance. Any diff => trading paused on venue."""
    tol = tolerance or {}
    diffs = []
    for asset in sorted(set(internal) | set(exchange)):
        a, b = internal.get(asset, Decimal(0)), exchange.get(asset, Decimal(0))
        if abs(a - b) > tol.get(asset, default_tolerance):
            diffs.append(BalanceDiff(asset, a, b))
    return diffs
