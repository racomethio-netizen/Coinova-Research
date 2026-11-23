"""Portfolio backtester for the global strategy library (one strategy at a time, all its
symbols, one timeframe).

Execution model (conservative, stated in the pre-registration):
* A decision at a bar's close is filled at the NEXT bar's open. A market buy pays the
  half-spread plus slippage. Its size is capped at 5% of that bar's volume (the rest is
  not filled) and rounded down to the lot step. It is refused below the minimum notional
  or if the open is already through the stop.
* Inside a bar, the stop is checked BEFORE the target. A gap through the stop fills at
  the open; otherwise the stop fills at its level, as a market order with taker fee,
  spread and slippage. The target is a resting limit sell (maker fee, no slippage).
* A strategy exit or the time barrier is decided at a close and filled at the next open
  (market).
* Sizing and portfolio limits use the owner's research profile (nexus_risk.spot_rules
  RESEARCH_PROFILE): 0.5% risk per trade from the stop distance including fees and
  slippage, at most 3 positions, one position per symbol (no averaging down), 1.5%
  aggregate open risk, and the 2% daily / 4% weekly loss limits.
  * The 5% portfolio-drawdown halt is NOT applied, so each strategy's edge is measured on
    its full history. The report states when the halt would have triggered.
* No leverage, no shorting, no borrowing.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from nexus_risk.spot_rules import (
    RESEARCH_PROFILE,
    BookState,
    Position,
    SpotRiskConfig,
    entry_checks,
    size,
)
from nexus_strategies.library_global import TF_SECONDS


@dataclass(frozen=True)
class Costs:
    taker_fee: float = 0.001
    maker_fee: float = 0.001
    half_spread_bps: dict[str, float] = field(default_factory=lambda: {"BTCUSDT": 0.25,
                                                                        "ETHUSDT": 0.25})
    default_half_spread_bps: float = 1.0
    slippage_bps: float = 5.0
    participation_max: float = 0.05

    def taker_cost(self, symbol: str) -> float:
        return (self.half_spread_bps.get(symbol, self.default_half_spread_bps)
                + self.slippage_bps) / 10_000


@dataclass(frozen=True)
class Rules:
    step: float = 1e-5
    min_notional: float = 5.0


@dataclass
class Open:
    pos: Position
    target: float
    time_limit: int
    bars: int = 0
    entry_notional: float = 0.0
    risk_money: float = 0.0


@dataclass
class LibResult:
    equity: pd.Series
    trades: pd.DataFrame
    signals: int
    rejected: dict[str, int]
    halt_would_trigger_at: str | None
    open_positions: list[dict[str, Any]] = field(default_factory=list)


def run(sigs: dict[str, pd.DataFrame], bars: dict[str, pd.DataFrame], tf: str, *,
        start: pd.Timestamp, end: pd.Timestamp, rules: dict[str, Rules] | None = None,
        costs: Costs | None = None, risk: SpotRiskConfig = RESEARCH_PROFILE,
        initial: float = 10_000.0) -> LibResult:
    """``sigs[symbol]``: library signals; ``bars[symbol]``: the same timeframe's OHLCV."""
    costs = costs or Costs()
    rules = rules or {}
    # Sizing assumes the same fees as the fills.
    risk = dataclasses.replace(risk, taker_fee=costs.taker_fee, maker_fee=costs.maker_fee)
    period = pd.Timedelta(seconds=TF_SECONDS[tf])
    clock = sorted({t for b in bars.values() for t in b.index if start <= t < end})
    st = BookState(initial, initial, [], initial, initial)
    held: dict[str, Open] = {}
    pending_in: list[dict[str, Any]] = []
    pending_out: dict[str, str] = {}
    trades: list[dict[str, Any]] = []
    eq: list[float] = []
    rejected: dict[str, int] = {}
    n_signals = 0
    peak = initial
    halt_at: str | None = None
    last_close: dict[str, float] = {}
    day = week = None

    def note(why: str) -> None:
        rejected[why] = rejected.get(why, 0) + 1

    def close(sym: str, t: pd.Timestamp, price: float, kind: str, taker: bool) -> None:
        o = held.pop(sym)
        p = o.pos
        fill = price * (1 - costs.taker_cost(sym)) if taker else price
        fee = fill * p.qty * (costs.taker_fee if taker else costs.maker_fee)
        st.cash += fill * p.qty - fee
        pnl = (fill - p.entry) * p.qty - fee - p.fees
        st.positions.remove(p)
        trades.append({"symbol": sym, "entry_time": p.opened_at, "exit_time": t,
                       "entry": p.entry, "exit": fill, "qty": p.qty, "exit_kind": kind,
                       "bars": o.bars, "pnl": pnl, "fees": p.fees + fee,
                       "ret": pnl / o.entry_notional if o.entry_notional else 0.0,
                       "r": pnl / o.risk_money if o.risk_money else 0.0})

    for t in clock:
        now = {s: b.loc[t] for s, b in bars.items() if t in b.index}
        # (1) exits decided at the previous close, at this open
        for sym, kind in list(pending_out.items()):
            if sym in now and sym in held:
                close(sym, t, float(now[sym]["open"]), kind, True)
                del pending_out[sym]
        # (2) entries decided at the previous close, at this open
        for req in pending_in:
            sym = req["symbol"]
            if sym not in now or sym in held:
                note("no data at the fill bar" if sym not in now else "already holding")
                continue
            b, r = now[sym], rules.get(sym, Rules())
            fill = float(b["open"]) * (1 + costs.taker_cost(sym))
            # Re-cap at the actual fill: a gap up must not raise the money at risk.
            per_unit = (fill - req["stop"]) + (fill + req["stop"]) * costs.taker_fee \
                + req["stop"] * costs.taker_cost(sym)
            qty = min(req["qty"], req["risk_budget"] / per_unit if per_unit > 0 else 0.0,
                      costs.participation_max * float(b["volume"]))
            qty = math.floor(qty / r.step + 1e-9) * r.step
            if qty <= 0 or qty * fill < r.min_notional:
                note("below minimum notional after the volume cap")
                continue
            fee = fill * qty * costs.taker_fee
            if fill * qty + fee > st.cash:
                note("insufficient cash")
                continue
            st.cash -= fill * qty + fee
            pos = Position(sym, req["strategy"], qty, fill, req["stop"], req["stop"], req["target"],
                           req["target"], t.to_pydatetime(), fees=fee)
            st.positions.append(pos)
            held[sym] = Open(pos, req["target"], req["time_limit"], entry_notional=fill * qty,
                             risk_money=(fill - req["stop"]) * qty + fee
                             + req["stop"] * qty * (costs.taker_fee + costs.taker_cost(sym)))
        pending_in = []
        # (3) stops before targets, inside this bar
        for sym in list(held):
            if sym not in now:
                continue
            b, o = now[sym], held[sym]
            op, hi, lo = float(b["open"]), float(b["high"]), float(b["low"])
            if op <= o.pos.stop:
                close(sym, t, op, "stop", True)
            elif lo <= o.pos.stop:
                close(sym, t, o.pos.stop, "stop", True)
            elif hi >= o.target:
                close(sym, t, max(o.target, op), "target", False)
            else:
                o.bars += 1
        # (4) mark to market, periods, the drawdown halt that is only reported
        for sym, b in now.items():
            last_close[sym] = float(b["close"])
        st.equity = st.cash + sum(o.pos.qty * last_close.get(s, o.pos.entry)
                                  for s, o in held.items())
        eq.append(st.equity)
        peak = max(peak, st.equity)
        if halt_at is None and st.equity < peak * (1 - 0.05):
            halt_at = str(t)
        close_time = (t + period).to_pydatetime()
        d, w = close_time.date(), close_time.isocalendar()[:2]
        if d != day:
            day, st.day_start_equity = d, st.equity
        if w != week:
            week, st.week_start_equity = w, st.equity
        # (5) decisions at this close
        for sym in sorted(now):
            s = sigs.get(sym)
            if s is None or t not in s.index:
                continue
            row = s.loc[t]
            if sym in held:
                o = held[sym]
                if bool(row["exit"]):
                    pending_out[sym] = "strategy_exit"
                elif o.bars >= o.time_limit:
                    pending_out[sym] = "time_limit"
                continue
            if not bool(row["entry"]):
                continue
            n_signals += 1
            c, stop = last_close[sym], float(row["stop"])
            reasons = entry_checks(st, sym, c, stop, close_time, risk, strategy="lib")
            if not reasons:
                r = rules.get(sym, Rules())
                qty, why = size(st.equity, st.cash, c, stop, risk,
                                slippage=costs.taker_cost(sym), step=r.step,
                                min_notional=r.min_notional)
                if why:
                    reasons.append(why)
                else:
                    pending_in.append({"symbol": sym, "strategy": "lib", "qty": qty,
                                       "risk_budget": risk.risk_per_trade * st.equity,
                                       "stop": stop, "target": float(row["target"]),
                                       "time_limit": int(row["time_limit"])})
            for why in reasons:
                note(why)
    tr = pd.DataFrame(trades)
    opened = [{"symbol": s, "entry_time": str(o.pos.opened_at), "entry": o.pos.entry,
               "qty": o.pos.qty, "stop": o.pos.stop, "target": o.target, "bars": o.bars,
               "time_limit": o.time_limit, "exit_pending": pending_out.get(s)}
              for s, o in held.items()]
    return LibResult(pd.Series(eq, index=pd.DatetimeIndex(clock[:len(eq)])), tr, n_signals,
                     rejected, halt_at, opened)


def daily_sharpe_sortino(equity: pd.Series) -> tuple[float | None, float | None]:
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) < 30 or daily.std() == 0:
        return None, None
    sh = float(daily.mean() / daily.std() * math.sqrt(365))
    down = daily[daily < 0]
    so = float(daily.mean() / down.std() * math.sqrt(365)) if len(down) > 1 else None
    return sh, so


def metrics(res: LibResult, initial: float = 10_000.0) -> dict[str, Any]:
    tr, e = res.trades, res.equity
    out: dict[str, Any] = {"trades": len(tr), "signals": res.signals,
                           "halt_5pct_would_trigger_at": res.halt_would_trigger_at}
    if len(e):
        out["return_pct"] = float(e.iloc[-1] / initial - 1)
        out["max_drawdown"] = float((1 - e / e.cummax()).max())
        out["sharpe"], out["sortino"] = daily_sharpe_sortino(e)
    if tr.empty:
        return out
    wins, losses = tr[tr.pnl > 0], tr[tr.pnl <= 0]
    gl = float(-losses.pnl.sum())
    net = float(tr.pnl.sum())
    out.update(net_profit=net, win_rate=float(len(wins) / len(tr)),
               profit_factor=float(wins.pnl.sum() / gl) if gl > 0 else None,
               avg_win=float(wins.pnl.mean()) if len(wins) else None,
               avg_loss=float(losses.pnl.mean()) if len(losses) else None,
               expectancy=float(tr.pnl.mean()), expectancy_r=float(tr.r.mean()),
               avg_ret=float(tr.ret.mean()), fees=float(tr.fees.sum()),
               exit_kinds={str(k): int(v) for k, v in tr.exit_kind.value_counts().items()},
               avg_bars=float(tr.bars.mean()))
    mdd_money = float((e.cummax() - e).max()) if len(e) else 0.0
    out["recovery_factor"] = net / mdd_money if mdd_money > 0 else None
    return out


def split(res: LibResult, start: pd.Timestamp, end: pd.Timestamp) -> LibResult:
    """The trades ENTERED within [start, end), and the equity over that window."""
    tr = res.trades
    sub = tr[(tr.entry_time >= start.to_pydatetime()) & (tr.entry_time < end.to_pydatetime())] \
        if not tr.empty else tr
    e = res.equity[(res.equity.index >= start) & (res.equity.index < end)]
    e = e / e.iloc[0] * 10_000.0 if len(e) else e
    return LibResult(e, sub.reset_index(drop=True), 0, {}, None)


def periods_held(tr: pd.DataFrame, tf: str) -> timedelta:
    return pd.Timedelta(seconds=TF_SECONDS[tf] * float(tr.bars.mean())) if len(tr) \
        else timedelta(0)


def per_symbol_pf(tr: pd.DataFrame) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for sym, g in tr.groupby("symbol"):
        gl = float(-g.pnl[g.pnl <= 0].sum())
        out[str(sym)] = float(g.pnl[g.pnl > 0].sum() / gl) if gl > 0 else None
    return out


def returns_array(tr: pd.DataFrame) -> np.ndarray:
    return tr.ret.to_numpy(dtype=float) if len(tr) else np.array([])
