"""Portfolio backtester for the spot strategy engine (docs/SPOT_ENGINE_PLAN.md).

Clock: 15m bars. A decision at a bar's CLOSE becomes an order filled at the NEXT bar's
open. Execution model:
* entries and stop exits are taker orders: half spread + slippage (size-dependent model
  from ``engine.SlippageModel``) + taker fee;
* take-profits are resting limits filled at their price with the maker fee;
* a partial fill is capped at ``participation_max`` of the fill bar's volume, and the rest
  is cancelled (IOC);
* random rejections at ``reject_rate`` (seeded, so runs are reproducible);
* Binance step and minimum-notional rules;
* a gap below the stop fills at the open;
* an emergency exit when a held symbol's data is missing or its liquidity collapses.

The risk and exit rules are ``nexus_risk.spot_rules``, the same code as the paper runner.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from nexus_risk.spot_rules import BookState, Position, SpotRiskConfig, entry_checks, manage_bar, size
from nexus_strategies import indicators as ind
from nexus_strategies.spot_regime import RegimeConfig, classify_spot
from nexus_strategies.spot_strategies import (
    BreakoutParams,
    TrendPullbackParams,
    breakout_retest,
    trend_pullback,
)

from nexus_backtest.engine import SlippageModel, liquidity_inputs

Q = pd.Timedelta(minutes=15)


@dataclass(frozen=True)
class SymbolRules:
    tick: float = 0.01
    step: float = 0.00001
    min_notional: float = 5.0


@dataclass
class EngineConfig:
    risk: SpotRiskConfig = field(default_factory=SpotRiskConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    pullback: TrendPullbackParams = field(default_factory=TrendPullbackParams)
    breakout: BreakoutParams = field(default_factory=BreakoutParams)
    strategies: tuple[str, ...] = ("trend_pullback", "breakout_retest_v2")
    spread_bps: dict[str, float] = field(default_factory=lambda: {"BTCUSDT": 0.5,
                                                                   "ETHUSDT": 0.5})
    default_spread_bps: float = 2.0
    slippage: SlippageModel = field(default_factory=lambda: SlippageModel(base_rate=0.0003))
    participation_max: float = 0.05
    reject_rate: float = 0.01
    seed: int = 7
    initial: float = 10_000.0
    correlation_window: int = 30 * 24 * 4       # 30 days of 15m bars


def resample(m15: pd.DataFrame, rule: str, n: int) -> pd.DataFrame:
    """Higher timeframe from COMPLETE groups of closed 15m bars only."""
    g = m15.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"})
    return out[g.size() == n].dropna()


def prepare(data: dict[str, pd.DataFrame], cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    """Per symbol: regime and signals of both strategies, keyed by the 15m bar whose close
    is the decision time."""
    btc = data.get("BTCUSDT")
    btc_h4 = resample(btc, "4h", 16) if btc is not None else None
    out: dict[str, dict[str, Any]] = {}
    for sym, m15 in data.items():
        h1, h4 = resample(m15, "1h", 4), resample(m15, "4h", 16)
        is_btc = sym == "BTCUSDT"
        reg15 = classify_spot(m15, "15m", h1, h4, btc_h4, is_btc=is_btc, cfg=cfg.regime)
        sigs = []
        if "trend_pullback" in cfg.strategies:
            tp = trend_pullback(m15, h1, reg15, cfg.pullback)
            tp = tp[tp["candidate"]].assign(strategy="trend_pullback", decide_at=lambda d: d.index)
            sigs.append(tp)
        if "breakout_retest_v2" in cfg.strategies:
            reg1h = classify_spot(h1, "1h", h1, h4, btc_h4, is_btc=is_btc, cfg=cfg.regime)
            bo = breakout_retest(h1, reg1h, cfg.breakout)
            bo = bo[bo["candidate"]].assign(strategy="breakout_retest_v2")
            bo["decide_at"] = bo.index + pd.Timedelta(minutes=45)   # last 15m bar of the hour
            sigs.append(bo)
        sig = pd.concat(sigs) if sigs else pd.DataFrame()
        out[sym] = {"m15": m15, "regime": reg15, "atr": ind.atr(m15, 14),
                    "liq": liquidity_inputs(m15, cfg.slippage.lookback_days),
                    "signals": {t: g for t, g in sig.groupby("decide_at")} if len(sig) else {}}
    return out


@dataclass
class Result:
    equity: pd.Series
    trades: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    exposure: float
    config: EngineConfig


def _clusters(data: dict[str, pd.DataFrame], t: pd.Timestamp, cfg: EngineConfig
              ) -> dict[str, set[str]]:
    rets = pd.DataFrame({s: d["close"].loc[:t].iloc[-cfg.correlation_window:].pct_change()
                         for s, d in data.items()})
    corr = rets.corr()
    return {s: {o for o in corr.columns if corr.loc[s, o] >= cfg.risk.correlation_threshold}
            | {s} for s in corr.columns}


def run(data: dict[str, pd.DataFrame], rules: dict[str, SymbolRules], cfg: EngineConfig,
        *, start: pd.Timestamp, end: pd.Timestamp) -> Result:
    rng = random.Random(cfg.seed)  # noqa: S311 (simulation only)
    prep = prepare(data, cfg)
    clock = sorted({t for d in data.values() for t in d.index if start <= t < end})
    rc = cfg.risk
    st = BookState(cfg.initial, cfg.initial, [], cfg.initial, cfg.initial)
    pending: list[dict[str, Any]] = []
    trades, signals, orders, eq_vals = [], [], [], []
    held_bars = 0
    last_close: dict[str, float] = {}
    day = week = None
    clusters_day = None

    def reject(o: dict[str, Any], t: pd.Timestamp, why: str) -> None:
        orders.append({**o, "time": t, "status": "rejected", "why": why})

    def half_spread(sym: str) -> float:
        return cfg.spread_bps.get(sym, cfg.default_spread_bps) / 2 / 10_000

    def slip(sym: str, t: pd.Timestamp, notional: float) -> float:
        liq = prep[sym]["liq"]
        if t not in liq.index:
            return cfg.slippage.max_rate
        dv, adv = liq.at[t, "daily_vol"], liq.at[t, "adv"]
        return cfg.slippage.rate(notional, 0.0 if np.isnan(dv) else float(dv),
                                 0.0 if np.isnan(adv) else float(adv))

    def close_position(p: Position, t: pd.Timestamp, price: float, kind: str, taker: bool) -> None:
        cost_rate = rc.taker_fee if taker else rc.maker_fee
        sl = (half_spread(p.symbol) + slip(p.symbol, t, price * p.qty)) if taker else 0.0
        fill = price * (1 - sl)
        fee = fill * p.qty * cost_rate
        st.cash += fill * p.qty - fee
        p.fees += fee
        pnl = p.realized + (fill - p.entry) * p.qty - fee
        p.realized = pnl
        total_qty = p.qty
        st.positions.remove(p)
        r_amount = p.r_per_unit * (p.qty if not p.tp1_done else p.qty / (1 - rc.tp1_fraction))
        trades.append({"symbol": p.symbol, "strategy": p.strategy, "regime": p.regime,
                       "entry_time": p.opened_at, "exit_time": t, "entry": p.entry,
                       "exit": fill, "exit_kind": kind, "qty_last": total_qty,
                       "pnl": pnl, "fees": p.fees, "r": pnl / r_amount if r_amount > 0 else 0.0,
                       "tp1_hit": p.tp1_done})
        orders.append({"time": t, "symbol": p.symbol, "side": "sell", "kind": kind,
                       "price": fill, "qty": total_qty, "fee": fee, "status": "filled",
                       "slippage": (price - fill) * total_qty})
        if pnl < 0:
            st.losses_in_a_row += 1
            if st.losses_in_a_row >= rc.loss_streak:
                st.cooldown_until = t + Q + rc.cooldown
                st.losses_in_a_row = 0
        else:
            st.losses_in_a_row = 0

    for t in clock:
        bars = {s: prep[s]["m15"].loc[t] for s in prep if t in prep[s]["m15"].index}
        # (1) Fill pending entries at this bar's open (latency: decided last close).
        for o in pending:
            sym = o["symbol"]
            if sym not in bars:
                reject(o, t, "no market data")
                continue
            b = bars[sym]
            if rng.random() < cfg.reject_rate:
                reject(o, t, "simulated exchange rejection")
                continue
            px = float(b["open"])
            qty = min(o["qty"], cfg.participation_max * float(b["volume"]))
            r = rules.get(sym, SymbolRules())
            qty = math.floor(qty / r.step + 1e-9) * r.step
            sl = half_spread(sym) + slip(sym, t, px * qty)
            fill = px * (1 + sl)
            if qty * fill < r.min_notional or qty <= 0:
                reject(o, t, "below minimum notional after partial fill")
                continue
            if fill <= o["stop"]:
                reject(o, t, "gapped below the stop")
                continue
            fee = fill * qty * rc.taker_fee
            if fill * qty + fee > st.cash:
                reject(o, t, "insufficient cash")
                continue
            st.cash -= fill * qty + fee
            # Levels keep the planned R multiples relative to the actual fill.
            rr = fill - o["stop"]
            pos = Position(sym, o["strategy"], qty, fill, o["stop"], o["stop"],
                           fill + rc_tp(cfg, o["strategy"], 1) * rr,
                           fill + rc_tp(cfg, o["strategy"], 2) * rr, t, o["regime"],
                           highest=fill, fees=fee, realized=-fee)
            st.positions.append(pos)
            orders.append({"time": t, "symbol": sym, "side": "buy", "kind": "entry",
                           "strategy": o["strategy"], "price": fill, "qty": qty,
                           "requested_qty": o["qty"], "fee": fee, "decided": o["decided"],
                           "slippage": (fill - px) * qty,
                           "status": "partial" if qty < o["qty"] - 1e-12 else "filled"})
        pending = []
        # (2) Manage open positions on this bar.
        for p in list(st.positions):
            if p.symbol not in bars:
                continue
            b = bars[p.symbol]
            reg = prep[p.symbol]["regime"]
            if t in reg.index and reg.at[t, "regime"] == "LOW_LIQUIDITY":
                close_position(p, t, float(b["open"]), "emergency", True)
                continue
            atr = prep[p.symbol]["atr"].get(t)
            before = p.qty
            fills = manage_bar(p, float(b["open"]), float(b["high"]), float(b["low"]),
                               float(b["close"]), None if atr is None else float(atr), rc,
                               slippage=slip(p.symbol, t, float(b["close"]) * p.qty))
            for f in fills:
                if f.kind == "tp1":
                    fill = f.price
                    fee = fill * f.qty * rc.maker_fee
                    st.cash += fill * f.qty - fee
                    p.fees += fee
                    p.realized += (fill - p.entry) * f.qty - fee
                    orders.append({"time": t, "symbol": p.symbol, "side": "sell",
                                   "kind": "tp1", "price": fill, "qty": f.qty, "fee": fee,
                                   "status": "filled"})
                    if any(x.kind == "tp2" for x in fills):   # both targets in one bar
                        p.qty, p.tp1_done = before - f.qty, True
                else:
                    close_position(p, t, f.price, f.kind, f.kind != "tp2")
                    break
        # Emergency exit: a held symbol without data this bar.
        for p in list(st.positions):
            if p.symbol not in bars and p.symbol in last_close:
                close_position(p, t, last_close[p.symbol] * 0.99, "emergency (no data)", True)
        # (3) Mark to market at the close; period boundaries.
        for s, b in bars.items():
            last_close[s] = float(b["close"])
        st.equity = st.cash + sum(p.qty * last_close.get(p.symbol, p.entry) for p in st.positions)
        eq_vals.append(st.equity)
        held_bars += bool(st.positions)
        d_key, w_key = t.date(), (t.isocalendar().year, t.isocalendar().week)
        if d_key != day:
            day, st.day_start_equity = d_key, st.equity
            clusters_day = None
        if w_key != week:
            week, st.week_start_equity = w_key, st.equity
        # (4) Decisions at this bar's close.
        for sym in bars:
            group = prep[sym]["signals"].get(t)
            if group is None:
                continue
            for _, row in group.iterrows():
                rec = {"time": t + Q, "symbol": sym, "strategy": row["strategy"],
                       "regime": prep[sym]["regime"]["regime"].get(t),
                       "close": last_close[sym], "stop": float(row["stop"]),
                       "tp1": float(row["tp1"]), "tp2": float(row["tp2"]),
                       "score": float(row["score"]), "status": "rejected",
                       "reasons": [] if bool(row["entry"]) else [str(row["reason"])]}
                if bool(row["entry"]):
                    if clusters_day is None:
                        clusters_day = _clusters({s: prep[s]["m15"] for s in prep}, t, cfg)
                        st.correlated = clusters_day
                    risk_mult = float(prep[sym]["regime"]["risk_mult"].get(t, 1.0))
                    reasons = entry_checks(st, sym, last_close[sym], float(row["stop"]),
                                           (t + Q).to_pydatetime(), rc, risk_mult=risk_mult)
                    if any(o["symbol"] == sym for o in pending):
                        reasons.append("order already pending for this symbol")
                    if not reasons:
                        r = rules.get(sym, SymbolRules())
                        qty, why = size(st.equity, st.cash, last_close[sym], float(row["stop"]),
                                        rc, risk_mult=risk_mult,
                                        slippage=half_spread(sym) + cfg.slippage.base_rate,
                                        step=r.step, min_notional=r.min_notional)
                        if why:
                            reasons.append(why)
                        else:
                            pending.append({"symbol": sym, "strategy": row["strategy"],
                                            "qty": qty, "stop": float(row["stop"]),
                                            "regime": rec["regime"], "decided": t + Q})
                            rec.update(status="accepted", qty=qty,
                                       risk=st.equity * rc.risk_per_trade * risk_mult)
                    rec["reasons"] = reasons
                signals.append(rec)
    # Close anything open at the end at the last close (marked, with taker costs).
    if clock:
        for p in list(st.positions):
            close_position(p, clock[-1], last_close[p.symbol], "end_of_test", True)
        if eq_vals:
            eq_vals[-1] = st.cash
    equity = pd.Series(eq_vals, index=pd.DatetimeIndex(clock), name="equity")
    return Result(equity, trades, signals, orders,
                  held_bars / len(clock) if clock else 0.0, cfg)


def rc_tp(cfg: EngineConfig, strategy: str, n: int) -> float:
    p = cfg.pullback if strategy == "trend_pullback" else cfg.breakout
    return p.tp1_r if n == 1 else p.tp2_r


def with_params(cfg: EngineConfig, **kw: Any) -> EngineConfig:
    """Copy with changed strategy parameters (``pullback__vol_mult=1.5`` style keys)."""
    new = replace(cfg)
    for k, v in kw.items():
        part, name = k.split("__")
        setattr(new, part, replace(getattr(new, part), **{name: v}))
    return new


__all__ = ["EngineConfig", "Result", "SymbolRules", "prepare", "resample", "run",
           "timedelta", "with_params"]
