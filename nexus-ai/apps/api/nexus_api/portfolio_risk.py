"""Builds the cross-market risk book (``nexus_risk.portfolio_book``) from what NEXUS knows:
* **Crypto paper book:** the latest equity snapshot (the trader writes one every
  5 minutes), the day and week starting equity, the peak, and the open paper positions
  with their stops.
* **Exness DEMO account:** the last demo sync (equity, NEXUS's positions with their
  stops, day/week/peak, quotes and contract sizes).

An account is "reconciled" only when its data is at most 15 minutes old and complete; an
unreconciled account blocks new entries (fails closed).

Correlations: Pearson correlation of the last 90 daily returns between instruments with
stored daily bars (the FX day for forex), used to group positions that move together.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from nexus_risk import portfolio_book as pb

FRESH = timedelta(minutes=15)
CORR_DAYS = 90
STATUS_KEY = "nexus:portfolio:risk"


def _first_after(s: Any, portfolio_id: int, at: datetime) -> Decimal | None:
    from nexus_db import models
    from sqlalchemy import select

    snap = models.PerformanceSnapshot
    out: Decimal | None = s.scalar(select(snap.equity).where(
        snap.portfolio_id == portfolio_id, snap.captured_at >= at)
        .order_by(snap.captured_at).limit(1))
    return out


def crypto_account(s: Any, now: datetime) -> pb.Account | None:
    from nexus_db import models, repo
    from sqlalchemy import select

    port = s.scalar(select(models.Portfolio).where(models.Portfolio.mode == "paper")
                    .order_by(models.Portfolio.id).limit(1))
    if port is None:
        return None
    snap = s.scalar(select(models.PerformanceSnapshot).where(
        models.PerformanceSnapshot.portfolio_id == port.id)
        .order_by(models.PerformanceSnapshot.captured_at.desc()).limit(1))
    if snap is None:
        return None
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    rows = s.execute(select(models.Position, models.TradingPair.symbol).join(
        models.TradingPair, models.TradingPair.id == models.Position.pair_id).where(
        models.Position.portfolio_id == port.id, models.Position.status == "open")).all()
    positions = []
    for p, sym in rows:
        last = repo.load_candles(s, p.pair_id, "5m", limit=1)
        price = last[-1].close if last else p.avg_entry_price
        positions.append(pb.Position("crypto_paper", "crypto", sym, 1, p.quantity,
                                     p.avg_entry_price, p.stop_loss, price))
    return pb.Account("crypto_paper", "crypto", snap.equity,
                      _first_after(s, port.id, day0) or snap.equity,
                      _first_after(s, port.id, week0) or snap.equity,
                      max(snap.peak_equity, snap.equity), tuple(positions),
                      now - snap.captured_at <= FRESH,
                      f"snapshot {snap.captured_at.isoformat()}")


def demo_account(demo: dict[str, Any] | None, now: datetime) -> pb.Account | None:
    if not demo:
        return None
    at = datetime.fromisoformat(demo["at"])
    symbols = demo.get("symbols") or {}
    lim = demo.get("limits") or {}
    positions, complete = [], True
    for p in demo.get("positions") or []:
        info = symbols.get(p["symbol"]) or {}
        size, base = info.get("contract_size"), info.get("base_name")
        if not size or not base:
            complete = False                      # cannot size it: fail closed
            continue
        bid, ask = Decimal(str(info["bid"])), Decimal(str(info["ask"]))
        positions.append(pb.Position(
            "exness_demo", "forex", base, 1 if p["side"] == "buy" else -1,
            Decimal(str(p["volume"])) * Decimal(str(size)), Decimal(str(p["price_open"])),
            Decimal(str(p["sl"])) if p.get("sl") else None, (bid + ask) / 2))
    equity = Decimal(str(demo["equity"]))
    return pb.Account("exness_demo", "forex", equity,
                      Decimal(str(lim.get("day_start") or equity)),
                      Decimal(str(lim.get("week_start") or equity)),
                      max(Decimal(str(lim.get("peak") or equity)), equity), tuple(positions),
                      complete and now - at <= FRESH,
                      f"demo sync {at.isoformat()}" + ("" if complete else
                                                       "; a position's contract size is "
                                                       "unknown"))


def build_book(s: Any, demo: dict[str, Any] | None, now: datetime) -> pb.Book:
    accounts = [a for a in (crypto_account(s, now), demo_account(demo, now)) if a is not None]
    return pb.Book(tuple(accounts), now)


def correlations(daily: dict[str, pd.DataFrame]) -> dict[frozenset[str], float]:
    """Correlation of the last 90 daily returns for every pair of instruments with at
    least 60 overlapping days. Missing pairs are left out (the book then falls back to
    currency overlap)."""
    rets = {k: np.log(v["close"]).diff().dropna().tail(CORR_DAYS)
            for k, v in daily.items() if v is not None and len(v) > 20}
    frame = pd.DataFrame({k: pd.Series(r.to_numpy(), index=r.index.normalize())
                          for k, r in rets.items()})
    out: dict[frozenset[str], float] = {}
    keys = list(frame.columns)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            both = frame[[a, b]].dropna()
            if len(both) >= 60:
                out[frozenset({a, b})] = float(both[a].corr(both[b]))
    return out


def summary(book: pb.Book, lim: pb.Limits, checks: list[pb.Check],
            corr: dict[frozenset[str], float]) -> dict[str, Any]:
    eq = book.total("equity")
    cents, frac = Decimal("0.01"), Decimal("0.000001")

    def usd(x: Decimal) -> str:
        return str(x.quantize(cents))

    def loss(start: Decimal) -> str | None:
        return str(((start - eq) / start).quantize(frac)) if start > 0 else None

    return {
        "at": book.as_of.isoformat(),
        "equity": usd(eq), "day_loss": loss(book.total("day_start")),
        "week_loss": loss(book.total("week_start")), "drawdown": loss(book.total("peak")),
        "open_risk_usd": usd(sum((p.risk_usd() for p in book.positions), Decimal(0))),
        "gross_notional_usd": usd(sum((p.notional_usd() for p in book.positions),
                                      Decimal(0))),
        "accounts": [{"name": a.name, "market": a.market, "equity": usd(a.equity),
                      "reconciled": a.reconciled, "note": a.note,
                      "positions": [{"symbol": p.symbol, "side": p.side,
                                     "quantity": str(p.quantity), "entry": str(p.entry),
                                     "stop": str(p.stop) if p.stop is not None else None,
                                     "price": str(p.price),
                                     "unrealized_usd": None if (u := p.unrealized_usd())
                                     is None else usd(u),
                                     "risk_usd": usd(p.risk_usd()),
                                     "notional_usd": usd(p.notional_usd())}
                                    for p in a.positions]} for a in book.accounts],
        "checks": [c.__dict__ for c in checks],
        "limits": {"risk_per_trade": str(lim.risk_per_trade),
                   "daily_loss": str(lim.daily_loss), "weekly_loss": str(lim.weekly_loss),
                   "drawdown_pause": str(lim.drawdown), "max_positions": lim.max_positions,
                   "max_open_risk": str(lim.max_open_risk),
                   "max_cluster_risk": str(lim.max_cluster_risk),
                   "min_reward_risk": str(lim.min_reward_risk),
                   "max_spot_crypto_exposure": str(lim.max_spot_crypto_exposure),
                   "max_gross_leverage": str(lim.max_gross_leverage),
                   "correlation_threshold": lim.correlation_threshold},
        "correlations": [{"a": sorted(k)[0], "b": sorted(k)[1], "rho": round(v, 2)}
                         for k, v in sorted(corr.items(), key=lambda kv: -abs(kv[1]))[:20]],
    }


def dumps(d: dict[str, Any]) -> str:
    return json.dumps(d, default=str)
