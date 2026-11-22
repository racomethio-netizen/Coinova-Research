"""Forward PAPER test of always-on funding carry (pre-registered:
docs/research/2026-11-22-carry-forward-paper-preregistration.md).

Long spot + short USD-M perpetual of equal notional per symbol, real Binance prices and
real settled funding; nothing is executed. Every event is appended to
``carry_paper_events`` with the full state after it. Money is Decimal throughout.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

log = get_logger("nexus.market.carry_paper")

RUN = "carry-2026-11-22"
CAPITAL = Decimal(10_000)
SYMBOLS = ("BTCUSDT", "ETHUSDT")
LEG = CAPITAL / len(SYMBOLS) / 2                 # N = 2,500 per leg
SPOT_COST = Decimal("0.0012")
PERP_COST = Decimal("0.0007")
REBALANCE_BAND = Decimal("0.05")
SPOT_API = "https://data-api.binance.vision/api/v3"
FAPI = "https://fapi.binance.com/fapi/v1"
D0 = Decimal(0)


@dataclass
class Leg:
    """Position state for one symbol."""
    spot_qty: Decimal = D0
    perp_qty: Decimal = D0            # size of the SHORT (positive number)
    perp_entry: Decimal = D0          # average short entry price
    cash: Decimal = D0                # USDT; perp margin stays in cash (1x)
    funding: Decimal = D0             # cumulative funding received (+) / paid (-)
    costs: Decimal = D0               # cumulative trading costs
    last_funding_ms: int = 0

    def to_json(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Leg:
        def dec(k: str) -> Decimal:
            return Decimal(str(d.get(k, "0")))
        return cls(spot_qty=dec("spot_qty"), perp_qty=dec("perp_qty"),
                   perp_entry=dec("perp_entry"), cash=dec("cash"), funding=dec("funding"),
                   costs=dec("costs"), last_funding_ms=int(d.get("last_funding_ms", 0)))


@dataclass(frozen=True)
class Quote:
    spot_bid: Decimal
    spot_ask: Decimal
    perp_bid: Decimal
    perp_ask: Decimal

    @property
    def spot_mid(self) -> Decimal:
        return (self.spot_bid + self.spot_ask) / 2

    @property
    def perp_mid(self) -> Decimal:
        return (self.perp_bid + self.perp_ask) / 2


def enter(q: Quote, now_ms: int) -> Leg:
    """Buy N of spot at the ask and short N of the perpetual at the bid, paying costs."""
    leg = Leg(cash=CAPITAL / len(SYMBOLS), last_funding_ms=now_ms)
    leg.spot_qty = LEG / q.spot_ask
    leg.perp_qty = LEG / q.perp_bid
    leg.perp_entry = q.perp_bid
    cost = LEG * SPOT_COST + LEG * PERP_COST
    leg.cash -= LEG + cost
    leg.costs += cost
    return leg


def apply_funding(leg: Leg, rate: Decimal, mark: Decimal, at_ms: int) -> Decimal:
    """A short RECEIVES positive funding and pays negative funding."""
    payment = leg.perp_qty * mark * rate
    leg.cash += payment
    leg.funding += payment
    leg.last_funding_ms = at_ms
    return payment


def equity(leg: Leg, q: Quote) -> Decimal:
    """Liquidation value at exit prices (spot bid, perp ask) net of exit costs."""
    spot = leg.spot_qty * q.spot_bid * (1 - SPOT_COST)
    perp_pnl = (leg.perp_entry - q.perp_ask) * leg.perp_qty
    perp_exit = leg.perp_qty * q.perp_ask * PERP_COST
    return leg.cash + spot + perp_pnl - perp_exit


def needs_rebalance(leg: Leg, q: Quote) -> bool:
    gap = abs(leg.spot_qty * q.spot_mid - leg.perp_qty * q.perp_mid)
    return gap > REBALANCE_BAND * LEG


def rebalance(leg: Leg, q: Quote) -> Decimal:
    """Reset both legs to equal notional (the average of the two) at mid prices, paying
    costs on the turnover. Perp P&L on any reduced size is realised into cash."""
    spot_val, perp_val = leg.spot_qty * q.spot_mid, leg.perp_qty * q.perp_mid
    target = (spot_val + perp_val) / 2
    new_spot, new_perp = target / q.spot_mid, target / q.perp_mid
    d_spot = new_spot - leg.spot_qty
    cost = abs(d_spot) * q.spot_mid * SPOT_COST + abs(new_perp - leg.perp_qty) * q.perp_mid * PERP_COST
    leg.cash -= d_spot * q.spot_mid + cost
    if new_perp < leg.perp_qty:
        leg.cash += (leg.perp_entry - q.perp_mid) * (leg.perp_qty - new_perp)
    else:
        leg.perp_entry = (leg.perp_entry * leg.perp_qty + q.perp_mid * (new_perp - leg.perp_qty)) / new_perp
    leg.spot_qty, leg.perp_qty = new_spot, new_perp
    leg.costs += cost
    return cost


# ------------------------------------------------------------------ data
async def fetch_quote(client: httpx.AsyncClient, symbol: str) -> Quote:
    s = await client.get(f"{SPOT_API}/ticker/bookTicker", params={"symbol": symbol})
    p = await client.get(f"{FAPI}/ticker/bookTicker", params={"symbol": symbol})
    for r in (s, p):
        if r.status_code == 451 or "restricted location" in r.text[:300].lower():
            raise PermissionError("Binance refuses this server's location")
        r.raise_for_status()
    sj, pj = s.json(), p.json()
    return Quote(Decimal(sj["bidPrice"]), Decimal(sj["askPrice"]),
                 Decimal(pj["bidPrice"]), Decimal(pj["askPrice"]))


async def fetch_funding(client: httpx.AsyncClient, symbol: str, after_ms: int) -> list[dict[str, Any]]:
    r = await client.get(f"{FAPI}/fundingRate", params={"symbol": symbol,
                                                       "startTime": after_ms + 1, "limit": 100})
    if r.status_code == 451 or "restricted location" in r.text[:300].lower():
        raise PermissionError("Binance refuses this server's location")
    r.raise_for_status()
    rows = [x for x in r.json() if int(x["fundingTime"]) > after_ms]
    return sorted(rows, key=lambda x: int(x["fundingTime"]))


# ------------------------------------------------------------------ ledger
class Ledger:
    """Append-only storage. ``latest`` returns the last state per symbol."""

    def __init__(self, factory: Any) -> None:
        self.factory = factory

    def latest(self) -> dict[str, Leg]:
        from nexus_db import models, transaction
        from sqlalchemy import select

        out: dict[str, Leg] = {}
        with transaction(self.factory) as s:
            for sym in SYMBOLS:
                row = s.scalar(select(models.CarryPaperEvent)
                               .where(models.CarryPaperEvent.run == RUN,
                                      models.CarryPaperEvent.symbol == sym,
                                      models.CarryPaperEvent.kind != "gap")
                               .order_by(models.CarryPaperEvent.id.desc()).limit(1))
                if row is not None and "state" in row.data:
                    out[sym] = Leg.from_json(row.data["state"])
        return out

    def append(self, symbol: str, kind: str, at: datetime, data: dict[str, Any]) -> bool:
        from nexus_db import models, transaction
        from sqlalchemy.exc import IntegrityError

        try:
            with transaction(self.factory) as s:
                s.add(models.CarryPaperEvent(run=RUN, symbol=symbol, kind=kind, at=at, data=data))
            return True
        except IntegrityError:                  # same event already recorded (idempotent)
            return False


async def step(ledger: Ledger, client: httpx.AsyncClient, now: datetime | None = None) -> dict[str, Any]:
    """One hourly pass: enter if needed, apply new settled funding, rebalance if outside
    the band, then record a mark. A data failure records a gap; nothing is estimated."""
    now = (now or utcnow()).replace(second=0, microsecond=0)
    hour = now.replace(minute=0)
    state = ledger.latest()
    summary: dict[str, Any] = {}
    for sym in SYMBOLS:
        try:
            q = await fetch_quote(client, sym)
            leg = state.get(sym)
            if leg is None:
                leg = enter(q, int(now.timestamp() * 1000))
                ledger.append(sym, "entry", now, {"quote": _q(q), "state": leg.to_json()})
            for f in await fetch_funding(client, sym, leg.last_funding_ms):
                at_ms = int(f["fundingTime"])
                mark = Decimal(str(f.get("markPrice") or q.perp_mid))
                pay = apply_funding(leg, Decimal(str(f["fundingRate"])), mark, at_ms)
                ledger.append(sym, "funding", datetime.fromtimestamp(at_ms / 1000, tz=UTC),
                              {"rate": str(f["fundingRate"]), "mark": str(mark),
                               "payment": str(pay), "state": leg.to_json()})
            if needs_rebalance(leg, q):
                cost = rebalance(leg, q)
                ledger.append(sym, "rebalance", now, {"quote": _q(q), "cost": str(cost),
                                                      "state": leg.to_json()})
            eq = equity(leg, q)
            ledger.append(sym, "mark", hour, {"quote": _q(q), "equity": str(eq),
                                              "state": leg.to_json()})
            summary[sym] = str(eq)
        except (httpx.HTTPError, PermissionError, KeyError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            ledger.append(sym, "gap", hour, {"reason": reason})
            log.warning("carry_paper_gap", symbol=sym, error=reason)
            summary[sym] = None
    log.info("carry_paper_step", equity=summary)
    return summary


def _q(q: Quote) -> dict[str, str]:
    return {k: str(v) for k, v in asdict(q).items()}


async def serve(settings: Any, stop: asyncio.Event, *, interval: float = 3600) -> None:
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value())
    ledger = Ledger(make_session_factory(engine))
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=True) as client:
            while not stop.is_set():
                try:
                    await step(ledger, client)
                except Exception as exc:  # keep the test running; the gap shows the failure
                    log.error("carry_paper_failed", error=f"{type(exc).__name__}: {exc}"[:300])
                now = utcnow()
                nxt = (now + timedelta(hours=1)).replace(minute=2, second=0, microsecond=0)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=min(interval,
                                                                   (nxt - now).total_seconds()))
                except TimeoutError:
                    pass
    finally:
        engine.dispose()


# ------------------------------------------------------------------ report
def report(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Summary for the dashboard from ledger rows (dicts with kind/symbol/at/data)."""
    marks: dict[datetime, dict[str, Decimal]] = {}
    gaps = 0
    funding = costs = D0
    start = None
    for e in events:
        if e["kind"] == "entry":
            start = min(start or e["at"], e["at"])
        if e["kind"] == "gap":
            gaps += 1
        if e["kind"] == "mark":
            marks.setdefault(e["at"], {})[e["symbol"]] = Decimal(e["data"]["equity"])
    latest: dict[str, Leg] = {}
    for e in sorted(events, key=lambda x: x.get("id") or 0):
        if "state" in e["data"]:
            latest[e["symbol"]] = Leg.from_json(e["data"]["state"])
    funding = sum((leg.funding for leg in latest.values()), D0)
    costs = sum((leg.costs for leg in latest.values()), D0)
    series = [(t, sum(v.values(), D0)) for t, v in sorted(marks.items()) if len(v) == len(SYMBOLS)]
    if start is None or not series:
        return {"run": RUN, "started": False, "capital": str(CAPITAL)}
    peak, max_dd = series[0][1], D0
    for _, eq in series:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    eq_now = series[-1][1]
    days = max((now - start).total_seconds() / 86400, 1e-9)
    ret = eq_now / CAPITAL - 1
    weeks: dict[str, list[Decimal]] = {}
    for t, eq in series:
        y, w, _ = t.isocalendar()
        weeks.setdefault(f"{y}-W{w:02d}", []).append(eq)
    complete = list(weeks.items())[1:-1]       # drop the partial first and current week
    prev = None
    pos = []
    for _, vals in complete:
        first = prev if prev is not None else vals[0]
        pos.append(vals[-1] > first)
        prev = vals[-1]
    hours = max(1, int(days * 24))
    decision_at = start + timedelta(days=90)
    return {
        "run": RUN, "started": True, "started_at": start.isoformat(), "days": round(days, 2),
        "decision_at": decision_at.isoformat(), "capital": str(CAPITAL),
        "equity": str(eq_now.quantize(Decimal("0.01"))), "return": str(ret.quantize(Decimal("0.000001"))),
        "annualised": None if days < 7 else str((ret * Decimal(365) / Decimal(str(days))).quantize(
            Decimal("0.0001"))),
        "max_drawdown": str(max_dd.quantize(Decimal("0.000001"))),
        "funding_received": str(funding.quantize(Decimal("0.01"))),
        "costs_paid": str(costs.quantize(Decimal("0.01"))),
        "complete_weeks": len(pos), "positive_weeks": sum(pos),
        "gap_hours": gaps, "gap_share": round(gaps / (hours * len(SYMBOLS)), 4),
        "series": [{"t": t.isoformat(), "equity": str(eq.quantize(Decimal("0.01")))}
                   for t, eq in series[-24 * 90:]],
        "gates": {"annualised_min": "0.04", "max_drawdown_max": "0.02",
                  "positive_weeks_min": "0.60", "gap_share_max": "0.05"},
        "decision": "pending" if now < decision_at else "due",
        "note": "Interim figures. The only decision is at day 90 (pre-registered).",
    }


async def main() -> None:
    import signal

    from nexus_common.logging import configure_logging
    from nexus_config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
