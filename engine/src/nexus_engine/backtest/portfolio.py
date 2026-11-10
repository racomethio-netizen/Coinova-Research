"""Portfolio accounting and risk rules for backtests (docs/nexus/SIGNAL_ENGINE.md §4).

Equity is realized equity (trades count when they close). Decimal throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class RiskRules:
    initial_equity: Decimal = Decimal(10_000)
    risk_per_trade: Decimal = Decimal("0.01")
    max_position_pct: Decimal = Decimal("0.25")
    max_concurrent: int = 3
    daily_loss_limit: Decimal = Decimal("0.03")
    max_drawdown_halt: Decimal = Decimal("0.15")

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class OpenPosition:
    direction: str
    closed_at: datetime
    pnl: Decimal


@dataclass
class Portfolio:
    rules: RiskRules
    equity: Decimal = Decimal(0)
    peak: Decimal = Decimal(0)
    halted_at: datetime | None = None
    open: list[OpenPosition] = field(default_factory=list)
    curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    day_start_equity: dict[date, Decimal] = field(default_factory=dict)
    day_pnl: dict[date, Decimal] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.equity = self.peak = self.rules.initial_equity

    def start(self, t: datetime) -> None:
        self.curve.append((t, self.equity))

    def realize_until(self, t: datetime) -> None:
        for pos in sorted((p for p in self.open if p.closed_at <= t), key=lambda p: p.closed_at):
            day = pos.closed_at.date()
            self.day_start_equity.setdefault(day, self.equity)
            self.equity += pos.pnl
            self.day_pnl[day] = self.day_pnl.get(day, Decimal(0)) + pos.pnl
            self.peak = max(self.peak, self.equity)
            self.curve.append((pos.closed_at, self.equity))
            if self.halted_at is None and self.drawdown() >= self.rules.max_drawdown_halt:
                self.halted_at = pos.closed_at
        self.open = [p for p in self.open if p.closed_at > t]

    def drawdown(self) -> Decimal:
        return (self.peak - self.equity) / self.peak if self.peak > 0 else Decimal(0)

    def can_open(self, t: datetime, direction: str) -> str | None:
        """None if a new position may open at t; otherwise the reason it may not."""
        if self.halted_at is not None:
            return "drawdown_halt"
        if any(p.direction == direction for p in self.open):
            return "open_position_same_direction"
        if len(self.open) >= self.rules.max_concurrent:
            return "max_concurrent"
        day = t.date()
        start = self.day_start_equity.get(day, self.equity)
        if self.day_pnl.get(day, Decimal(0)) <= -self.rules.daily_loss_limit * start:
            return "daily_loss_limit"
        return None

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def size(self, entry: Decimal, stop: Decimal) -> Decimal:
        """Fixed-fractional sizing, capped by the maximum position notional."""
        per_unit_risk = abs(entry - stop)
        if per_unit_risk <= 0:
            return Decimal(0)
        qty = self.equity * self.rules.risk_per_trade / per_unit_risk
        cap = self.equity * self.rules.max_position_pct / entry
        return min(qty, cap).quantize(Decimal("1e-12"), rounding=ROUND_DOWN)

    def add(self, pos: OpenPosition) -> None:
        self.open.append(pos)
