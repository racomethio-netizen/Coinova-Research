"""Balance reconciliation: our internal ledger versus what the exchange reports.

Live mode only (a paper ledger IS the truth). The rule is one-sided on purpose: the
exchange must hold AT LEAST what NEXUS believes it holds, per asset, within a small
tolerance. The account may hold more (the owner's own funds that NEXUS does not manage),
but never less. "Less" means NEXUS would size, exit or report from money or coins that are
not there: every new entry is blocked until the owner investigates.

Never auto-corrects either side: a mismatch is reported, audit-logged and alerted, and the
ledger is left exactly as it was. A failed or banned balance fetch is UNAVAILABLE, which
also blocks entries (unknown state = do not trade).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from nexus_common.decimals import ZERO
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_db import models, repo, transaction
from nexus_exchange.base import ExchangeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

log = get_logger("nexus.execution.reconcile")

REL_TOLERANCE = Decimal("0.001")       # 0.1% of the internal amount (fees, rounding)
ABS_TOLERANCE = Decimal("0.00000001")  # one satoshi-sized unit
STATUS_KEY = "nexus:reconcile:{mode}"
ACCOUNT_BAN_KEY = "nexus:binance:ban:production"   # written by nexus_api.binance_ban


class ReconStatus(StrEnum):
    OK = "ok"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"   # paper mode


@dataclass(frozen=True)
class Mismatch:
    exchange: str
    asset: str
    internal: str
    exchange_total: str
    shortfall: str


@dataclass(frozen=True)
class ReconResult:
    status: ReconStatus
    checked_at: datetime
    mismatches: tuple[Mismatch, ...] = ()
    unmanaged: dict[str, str] = field(default_factory=dict)   # exchange holds more (info)
    detail: str | None = None

    @property
    def blocks_entries(self) -> bool:
        return self.status in (ReconStatus.MISMATCH, ReconStatus.UNAVAILABLE)

    def to_json(self) -> str:
        d = asdict(self)
        d["status"] = self.status.value
        d["checked_at"] = self.checked_at.isoformat()
        return json.dumps(d)


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def tolerance(internal: Decimal) -> Decimal:
    return max(abs(internal) * REL_TOLERANCE, ABS_TOLERANCE)


def compare(exchange: str, internal: Mapping[str, Decimal],
            reported: Mapping[str, Decimal]) -> tuple[list[Mismatch], dict[str, str]]:
    """Pure comparison of total (free + locked) amounts per asset."""
    mismatches: list[Mismatch] = []
    unmanaged: dict[str, str] = {}
    for asset in sorted(set(internal) | set(reported)):
        ours = internal.get(asset, ZERO)
        theirs = reported.get(asset, ZERO)
        if theirs + tolerance(ours) < ours:
            mismatches.append(Mismatch(exchange, asset, _fmt(ours), _fmt(theirs),
                                       _fmt(ours - theirs)))
        elif theirs > ours + tolerance(ours):
            unmanaged[f"{exchange}:{asset}"] = _fmt(theirs - ours)
    return mismatches, unmanaged


def internal_totals(s: Session, portfolio_id: int, exchange: str) -> dict[str, Decimal]:
    """What NEXUS believes it holds on one venue: ledger cash rows plus open positions'
    base quantities (positions are tracked separately from the quote ledger)."""
    ex_id = repo.exchange_id(s, exchange)
    out: dict[str, Decimal] = {}
    for b in s.scalars(select(models.Balance).where(
            models.Balance.portfolio_id == portfolio_id, models.Balance.exchange_id == ex_id,
            models.Balance.source == "internal")):
        out[b.asset] = out.get(b.asset, ZERO) + b.free + b.locked
    for p in s.scalars(select(models.Position).where(
            models.Position.portfolio_id == portfolio_id, models.Position.exchange_id == ex_id,
            models.Position.status == "open")):
        if p.base_asset not in out:   # the ledger may already carry the base asset
            out[p.base_asset] = out.get(p.base_asset, ZERO) + p.quantity
    return {a: v for a, v in out.items() if v != 0}


def store_reported(s: Session, portfolio_id: int, mode: str, exchange: str,
                   reported: Mapping[str, tuple[Decimal, Decimal]]) -> None:
    """Keep the exchange's last report in balances(source='exchange') for the dashboard."""
    ex_id = repo.exchange_id(s, exchange)
    now = utcnow()
    rows = {b.asset: b for b in s.scalars(select(models.Balance).where(
        models.Balance.portfolio_id == portfolio_id, models.Balance.exchange_id == ex_id,
        models.Balance.source == "exchange"))}
    for asset, (free, locked) in reported.items():
        row = rows.get(asset)
        if row is None:
            s.add(models.Balance(portfolio_id=portfolio_id, mode=mode, exchange_id=ex_id,
                                 asset=asset, free=free, locked=locked, source="exchange",
                                 synced_at=now))
        else:
            row.free, row.locked, row.synced_at = free, locked, now
    for asset, row in rows.items():
        if asset not in reported:
            row.free, row.locked, row.synced_at = ZERO, ZERO, now


class BalanceReconciler:
    """Runs at most every ``interval`` seconds (account calls are expensive and rate
    limited); between runs the last result stands."""

    def __init__(self, factory: sessionmaker[Session], redis: Any, mode: str,
                 adapters: Mapping[str, ExchangeAdapter], *, interval: float = 60.0) -> None:
        self.factory, self.redis, self.mode = factory, redis, mode
        self.adapters, self.interval = adapters, interval
        self.last: ReconResult | None = None

    async def _banned(self, exchange: str) -> str | None:
        if exchange != "binance":
            return None
        try:
            raw = await self.redis.get(ACCOUNT_BAN_KEY)
        except Exception:
            return "ban state unreadable"
        if not raw:
            return None
        until = raw.decode() if isinstance(raw, bytes) else str(raw)
        return f"Binance account API banned until {until}"

    async def run(self, portfolio_id: int, *, force: bool = False) -> ReconResult:
        now = utcnow()
        if self.mode != "live":
            result = ReconResult(ReconStatus.NOT_APPLICABLE, now,
                                 detail="paper mode: the internal ledger is the record")
            return await self._publish(result)
        if (not force and self.last is not None
                and (now - self.last.checked_at).total_seconds() < self.interval):
            return self.last
        mismatches: list[Mismatch] = []
        unmanaged: dict[str, str] = {}
        for code, adapter in self.adapters.items():
            ban = await self._banned(code)
            if ban:
                return await self._publish(ReconResult(ReconStatus.UNAVAILABLE, now,
                                                       detail=ban))
            try:
                balances = await adapter.fetch_balances()
            except Exception as exc:   # any failure = state unknown = block entries
                return await self._publish(ReconResult(
                    ReconStatus.UNAVAILABLE, now,
                    detail=f"{code}: balance fetch failed ({type(exc).__name__})"))
            reported = {a: (b.free, b.locked) for a, b in balances.items() if b.total > 0}
            with transaction(self.factory) as s:
                store_reported(s, portfolio_id, self.mode, code, reported)
                ours = internal_totals(s, portfolio_id, code)
            m, u = compare(code, ours, {a: f + lk for a, (f, lk) in reported.items()})
            mismatches += m
            unmanaged.update(u)
        status = ReconStatus.MISMATCH if mismatches else ReconStatus.OK
        return await self._publish(ReconResult(status, now, tuple(mismatches), unmanaged))

    async def _publish(self, result: ReconResult) -> ReconResult:
        previous = self.last.status if self.last else None
        self.last = result
        try:
            await self.redis.set(STATUS_KEY.format(mode=self.mode), result.to_json(), ex=3600)
        except Exception as exc:
            log.error("reconcile_status_unwritable", error=type(exc).__name__)
        if result.status is not previous and result.status is not ReconStatus.NOT_APPLICABLE:
            with transaction(self.factory) as s:
                repo.record_audit(s, actor="system:reconcile",
                                  action=f"balance_reconciliation_{result.status.value}",
                                  details=json.loads(result.to_json()))
            if result.blocks_entries:
                log.error("balance_reconciliation_blocking", status=result.status.value,
                          detail=result.detail, mismatches=len(result.mismatches))
        return result
