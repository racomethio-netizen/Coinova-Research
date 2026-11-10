"""Bar-by-bar backtest of a strategy on stored candles.

Parity with production: each decision uses exactly the same lookback window as
the live SignalService and the same `strategy.evaluate`; trades are simulated
with the same execution model as the live outcome resolver (decide at the close,
use only later bars). Assumptions are returned with every result.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..marketdata.types import Candle
from ..signals.execution import Bar, Plan, resolve, unit_basis_and_pnl
from ..signals.series import HIGHER_TIMEFRAME, CandleSeries
from ..signals.strategy import Decision, Params, evaluate
from ..timeframes import tf_delta
from .portfolio import OpenPosition, Portfolio, RiskRules


@dataclass(frozen=True)
class Trade:
    seq: int
    direction: str
    decided_at: datetime
    closed_at: datetime
    outcome: str
    stop_loss: Decimal
    target: Decimal
    entry_price: Decimal | None
    exit_price: Decimal | None
    quantity: Decimal
    net_return: Decimal | None
    r_multiple: Decimal | None
    pnl: Decimal


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[datetime, Decimal]]
    final_equity: Decimal
    decisions: int
    open_at_end: int
    skipped: dict[str, int]
    halted_at: datetime | None
    assumptions: dict[str, object] = field(default_factory=dict)


def iter_decisions(
    tf: str, candles: Sequence[Candle], htf_candles: Sequence[Candle] | None, start: datetime, end: datetime, params: Params
) -> Iterator[tuple[int, datetime, Decision]]:
    """(index, as_of, decision) for each close in [start, end], with production lookback windows."""
    own = CandleSeries.from_candles(tf, candles)
    htf_tf = HIGHER_TIMEFRAME[tf]
    htf = CandleSeries.from_candles(htf_tf, htf_candles) if htf_tf and htf_candles else None
    htf_close = [t + tf_delta(htf_tf) for t in htf.open_times] if htf is not None and htf_tf else []
    step = tf_delta(tf)
    lookback = params.min_bars + 50  # identical to SignalService
    for i, t in enumerate(own.open_times):
        as_of = t + step
        if as_of < start or as_of > end:
            continue
        htf_w = None
        if htf is not None:
            k = bisect_right(htf_close, as_of) - 1
            htf_w = htf.window(k, lookback) if k >= 0 else None
        yield i, as_of, evaluate(own.window(i, lookback), htf_w, as_of, params)


def run_backtest(
    tf: str,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] | None,
    start: datetime,
    end: datetime,
    params: Params | None = None,
    rules: RiskRules | None = None,
) -> BacktestResult:
    params = params or Params()
    rules = rules or RiskRules()
    lookback = params.min_bars + 50
    bars = [Bar(c.open_time, c.open, c.high, c.low, c.close) for c in sorted((c for c in candles if c.is_final), key=lambda c: c.open_time)]

    pf = Portfolio(rules)
    pf.start(start)
    trades: list[Trade] = []
    decisions = open_at_end = 0
    for i, as_of, d in iter_decisions(tf, candles, htf_candles, start, end, params):
        pf.realize_until(as_of)
        decisions += 1
        if d.decision == "HOLD" or d.proposal is None:
            continue
        reason = pf.can_open(as_of, d.decision)
        if reason:
            pf.skip(reason)
            continue
        p = d.proposal
        plan = Plan(d.decision, p.entry_low, p.entry_high, p.stop_loss, p.take_profits[0], params.entry_window_bars, p.horizon_bars, params.costs)
        res = resolve(plan, bars[i + 1 : i + 2 + params.entry_window_bars + p.horizon_bars], tf, as_of)
        if res.outcome is None or res.closed_at is None:
            open_at_end += 1
            continue
        qty, pnl = Decimal(0), Decimal(0)
        if res.entry_price is not None and res.exit_price is not None:
            qty = pf.size(res.entry_price, p.stop_loss)
            _, unit_pnl = unit_basis_and_pnl(d.decision, res.entry_price, res.exit_price, params.costs)
            pnl = (qty * unit_pnl).quantize(Decimal("1e-8"))
            pf.add(OpenPosition(d.decision, res.closed_at, pnl))
        trades.append(
            Trade(
                len(trades) + 1,
                d.decision,
                as_of,
                res.closed_at,
                res.outcome,
                p.stop_loss,
                p.take_profits[0],
                res.entry_price,
                res.exit_price,
                qty,
                res.net_return,
                res.r_multiple,
                pnl,
            )
        )
    pf.realize_until(datetime.max.replace(tzinfo=start.tzinfo))
    return BacktestResult(
        trades=trades, equity_curve=pf.curve, final_equity=pf.equity, decisions=decisions, open_at_end=open_at_end,
        skipped=pf.skipped, halted_at=pf.halted_at,
        assumptions={
            "fills": "decide at candle close; entry/exit simulated on later bars only; stop wins ties; gaps fill at the open",
            "costs": params.costs.as_dict(), "risk": rules.as_dict(), "strategy_params": params.as_dict(),
            "equity": "realized (positions count when closed)", "lookback_bars": lookback,
        },
    )  # fmt: skip
