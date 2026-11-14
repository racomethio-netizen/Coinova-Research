"""Event-driven portfolio backtester.

Timing model (prevents look-ahead):
* A strategy row with open_time t is evaluated at t + base_period (its close).
* Its entry/exit executes at the OPEN of the 5m execution bar starting at that time.
* Stops/targets are checked on each later 5m bar's high/low. If both are touched in
  the same bar, the STOP is assumed first. A bar opening beyond a level fills at the
  open (gap-through), not at the level.
* Every fill pays the taker fee and adverse slippage.

Risk model: identical code to live (``nexus_risk``): sizing from equity marked at the
previous close, max positions, no duplicate asset exposure, max exposure, daily/weekly
loss limits (block new entries for the rest of the period) and the max-drawdown
shutdown (no new entries for the rest of the backtest).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from nexus_config import RiskSettings
from nexus_risk.limits import loss_fraction
from nexus_risk.sizing import SizingInput, size_position
from nexus_strategies.base import Strategy
from nexus_strategies.frames import TF_SECONDS

D = Decimal
ZERO = D(0)

SignalFilter = Callable[[str, str, pd.Timestamp, pd.Series], bool]


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    base_asset: str
    amount_step: Decimal
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None


@dataclass
class BacktestConfig:
    initial_capital: Decimal = field(default_factory=lambda: D(10_000))
    fee_rate: Decimal = field(default_factory=lambda: D("0.001"))  # 10 bps/side (base taker)
    slippage_rate: Decimal = field(default_factory=lambda: D("0.0005"))  # 5 bps per side
    risk: RiskSettings = field(default_factory=RiskSettings)
    execution_tf: str = "5m"


@dataclass
class Trade:
    strategy: str
    symbol: str
    entry_time: pd.Timestamp
    entry_price: Decimal
    quantity: Decimal
    stop: Decimal
    target: Decimal
    entry_fee: Decimal
    regime: str | None
    exit_time: pd.Timestamp | None = None
    exit_price: Decimal | None = None
    exit_fee: Decimal = ZERO
    exit_reason: str | None = None

    @property
    def pnl(self) -> Decimal:
        assert self.exit_price is not None
        return (self.exit_price - self.entry_price) * self.quantity - self.entry_fee - self.exit_fee

    @property
    def cost(self) -> Decimal:
        return self.entry_price * self.quantity + self.entry_fee


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series                     # mark-to-market equity at each bar close
    events: list[tuple[pd.Timestamp, str, str]]
    rejected_signals: dict[str, int]
    config: BacktestConfig

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "strategy": t.strategy, "symbol": t.symbol, "entry_time": t.entry_time,
            "exit_time": t.exit_time, "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": float(t.quantity), "pnl": float(t.pnl), "fees": float(t.entry_fee + t.exit_fee),
            "return": float(t.pnl / t.cost), "exit_reason": t.exit_reason, "regime": t.regime,
        } for t in self.trades if t.exit_price is not None])


@dataclass
class _Pending:
    strategy: str
    symbol: str
    stop: float
    target: float
    regime: str | None
    signal_close: float


def _q(x: float) -> Decimal:
    return D(repr(float(x)))


def run_backtest(
    data: dict[str, dict[str, pd.DataFrame]],
    strategies: list[Strategy],
    markets: dict[str, MarketSpec],
    config: BacktestConfig | None = None,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    signal_filter: SignalFilter | None = None,
) -> BacktestResult:
    """``data[symbol][timeframe]`` = OHLCV frame. Signals are computed on the full
    history (indicator warm-up) but only acted on within [start, end)."""
    cfg = config or BacktestConfig()
    risk = cfg.risk
    exec_tf = cfg.execution_tf
    step = pd.Timedelta(seconds=TF_SECONDS[exec_tf])

    # 1) Precompute signals, keyed by the execution time (signal bar close).
    entries: dict[pd.Timestamp, list[_Pending]] = {}
    exits: dict[pd.Timestamp, set[tuple[str, str]]] = {}
    for symbol, frames in data.items():
        for strat in strategies:
            sig = strat.compute(frames)
            sig = sig[sig["entry"] | sig["exit"]]
            avail = sig.index + pd.Timedelta(seconds=TF_SECONDS[strat.base_tf])
            close = frames[strat.base_tf]["close"]
            for t_avail, (t, row) in zip(avail, sig.iterrows(), strict=True):
                if (start is not None and t_avail < start) or (end is not None and t_avail >= end):
                    continue
                if row["exit"]:
                    exits.setdefault(t_avail, set()).add((strat.code, symbol))
                if row["entry"]:
                    if signal_filter is not None and not signal_filter(strat.code, symbol, t, row):
                        continue
                    entries.setdefault(t_avail, []).append(_Pending(
                        strat.code, symbol, float(row["stop"]), float(row["target"]),
                        row["regime"], float(close.loc[t])))

    # 2) Execution clock: union of execution-bar times within the window.
    bars = {s: f[exec_tf] for s, f in data.items()}
    clock = sorted(set().union(*(b.index for b in bars.values())))
    clock = [t for t in clock if (start is None or t >= start) and (end is None or t < end)]
    arrays = {s: {c: b[c].reindex(clock).to_numpy(dtype=float)
                  for c in ("open", "high", "low", "close")} for s, b in bars.items()}

    cash = cfg.initial_capital
    open_trades: list[Trade] = []
    done: list[Trade] = []
    events: list[tuple[pd.Timestamp, str, str]] = []
    rejected: dict[str, int] = {}
    equity_values = np.empty(len(clock))
    last_close: dict[str, Decimal] = {}
    peak = cash
    day_key = week_key = None
    day_start = week_start = cash
    blocked_until_day = blocked_until_week = None
    shutdown = False

    def mark() -> Decimal:
        return cash + sum((last_close.get(t.symbol, t.entry_price) * t.quantity
                           for t in open_trades), ZERO)

    def close_trade(tr: Trade, price: Decimal, when: pd.Timestamp, reason: str) -> None:
        nonlocal cash
        fill = price * (1 - cfg.slippage_rate)
        fee = fill * tr.quantity * cfg.fee_rate
        cash += fill * tr.quantity - fee
        tr.exit_price, tr.exit_fee, tr.exit_time, tr.exit_reason = fill, fee, when, reason
        open_trades.remove(tr)
        done.append(tr)

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for i, t in enumerate(clock):
        # Period bookkeeping uses equity at the previous close.
        eq_prev = mark()
        d_key, w_key = t.date(), (t.isocalendar().year, t.isocalendar().week)
        if d_key != day_key:
            day_key, day_start = d_key, eq_prev
        if w_key != week_key:
            week_key, week_start = w_key, eq_prev

        # (a) strategy exits at this bar's open
        for tr in list(open_trades):
            if (tr.strategy, tr.symbol) in exits.get(t, ()):
                o = arrays[tr.symbol]["open"][i]
                if not np.isnan(o):
                    close_trade(tr, _q(o), t, "strategy_exit")

        # (b) entries at this bar's open
        for p in entries.get(t, []):
            open_px = arrays[p.symbol]["open"][i]
            if np.isnan(open_px):
                reject("no execution bar")
                continue
            if shutdown:
                reject("drawdown shutdown")
                continue
            if blocked_until_day == day_key or blocked_until_week == week_key:
                reject("loss limit")
                continue
            if len(open_trades) >= risk.max_open_positions:
                reject("max positions")
                continue
            spec = markets[p.symbol]
            if any(markets[o.symbol].base_asset == spec.base_asset for o in open_trades):
                reject("duplicate asset")
                continue
            if open_px <= p.stop or open_px >= p.target:
                reject("gapped beyond levels")
                continue
            equity = mark()
            exposure = sum((last_close.get(o.symbol, o.entry_price) * o.quantity
                            for o in open_trades), ZERO)
            entry = _q(open_px) * (1 + cfg.slippage_rate)
            sizing = size_position(SizingInput(
                equity=equity, cash_available=cash, entry_price=entry, stop_price=_q(p.stop),
                risk_per_trade=risk.risk_per_trade, fee_rate=cfg.fee_rate,
                slippage_rate=cfg.slippage_rate, exposure_now=exposure,
                max_exposure=risk.max_portfolio_exposure, asset_exposure_now=ZERO,
                max_asset_concentration=risk.max_asset_concentration,
                amount_step=spec.amount_step, min_amount=spec.min_amount,
                min_notional=spec.min_notional))
            if not sizing.approved:
                reject("sizing")
                continue
            fee = entry * sizing.quantity * cfg.fee_rate
            cash -= entry * sizing.quantity + fee
            open_trades.append(Trade(p.strategy, p.symbol, t, entry, sizing.quantity,
                                     _q(p.stop), _q(p.target), fee, p.regime))

        # (c) intrabar stop / target (stop first when both touched)
        for tr in list(open_trades):
            arr = arrays[tr.symbol]
            if np.isnan(arr["open"][i]):
                continue
            o, h, lo = _q(arr["open"][i]), _q(arr["high"][i]), _q(arr["low"][i])
            if o <= tr.stop:
                close_trade(tr, o, t, "stop_gap")
            elif lo <= tr.stop:
                close_trade(tr, tr.stop, t, "stop")
            elif o >= tr.target:
                close_trade(tr, o, t, "target_gap")
            elif h >= tr.target:
                close_trade(tr, tr.target, t, "target")

        # (d) mark to market at close; risk-limit and drawdown checks
        for s, arr in arrays.items():
            c = arr["close"][i]
            if not np.isnan(c):
                last_close[s] = _q(c)
        equity = mark()
        equity_values[i] = float(equity)
        peak = max(peak, equity)
        if not shutdown and loss_fraction(peak, equity) >= risk.max_drawdown:
            shutdown = True
            events.append((t, "max_drawdown_shutdown", f"equity {equity:.2f} peak {peak:.2f}"))
            for tr in list(open_trades):
                close_trade(tr, last_close[tr.symbol], t + step, "shutdown")
        if blocked_until_day != day_key and loss_fraction(day_start, equity) >= risk.daily_loss_limit:
            blocked_until_day = day_key
            events.append((t, "daily_loss_limit", f"{loss_fraction(day_start, equity):.4%}"))
        if blocked_until_week != week_key and \
                loss_fraction(week_start, equity) >= risk.weekly_loss_limit:
            blocked_until_week = week_key
            events.append((t, "weekly_loss_limit", f"{loss_fraction(week_start, equity):.4%}"))

    # Close anything still open at the final close (marked, with costs).
    if clock:
        for tr in list(open_trades):
            close_trade(tr, last_close[tr.symbol], clock[-1] + step, "end_of_data")
        if len(equity_values):
            equity_values[-1] = float(mark())
    equity_series = pd.Series(equity_values, index=pd.DatetimeIndex(clock), name="equity")
    return BacktestResult(done, equity_series, events, rejected, cfg)


def result_summary(result: BacktestResult) -> dict[str, Any]:
    from nexus_backtest.metrics import compute_metrics

    return compute_metrics(result)
