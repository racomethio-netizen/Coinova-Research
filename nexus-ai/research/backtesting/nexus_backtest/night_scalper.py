"""Night scalper test on Exness tick-built M5 bars (bid and ask).

Pre-registration: docs/research/2026-11-27-night-scalper-preregistration.md. These are the
public idea of commercial "night scalper" bots: fade Bollinger/RSI extremes in the quiet
hours after the New York close, one position per pair with a stop, a fixed target at
the middle band, and a time exit. The rules are fixed, not fitted, so the whole history
is out of sample.

* Every fill is on the real side of the quote. A buy pays the ask and a sell gets the
  bid, from the bar's bid and ask OHLC.
* Profits in a cross's quote currency are converted to USD at the conversion pair's bid
  at that time.
* NEXUS risk runs across all pairs on one shared book.

Usage: ``python -m nexus_backtest.night_scalper --standard DIR --raw DIR --out FILE``
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from nexus_config.settings import (
    DEFAULT_DAILY_LOSS,
    DEFAULT_DRAWDOWN,
    DEFAULT_OPEN_POSITIONS,
    DEFAULT_OPEN_RISK,
    DEFAULT_RISK_PER_TRADE,
    DEFAULT_WEEKLY_LOSS,
)
from nexus_strategies.indicators import adx, atr, bollinger, rsi

from nexus_backtest import nqs1_bt
from nexus_backtest.significance import stationary_bootstrap_pvalue

PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "USDCHF", "USDCAD", "EURGBP", "EURCHF", "EURAUD",
         "EURCAD", "GBPCAD", "AUDCAD", "AUDNZD")
CONVERSION = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD")
NY = ZoneInfo("America/New_York")
EQUITY = 10_000.0
PREREG = "docs/research/2026-11-27-night-scalper-preregistration.md"


@dataclass(frozen=True)
class Rules:
    bb_n: int = 20
    bb_k: float = 2.0
    rsi_n: int = 14
    atr_n: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    adx_max: float = 25.0
    stop_atr: float = 2.0
    min_reward_atr: float = 0.5
    max_bars: int = 36
    start: time = time(17, 30)          # New York time, decision = bar close
    end: time = time(21, 0)
    flat: time = time(23, 0)
    slip_pips: float = 0.2
    commission_per_100k: float = 0.0    # USD per 100,000 base units, round trip
    cost_mult: float = 1.0              # 2.0 = the 2x cost stress


@dataclass(frozen=True)
class Risk:
    per_trade: float = float(DEFAULT_RISK_PER_TRADE)
    daily: float = float(DEFAULT_DAILY_LOSS)
    weekly: float = float(DEFAULT_WEEKLY_LOSS)
    drawdown: float = float(DEFAULT_DRAWDOWN)
    max_open: int = DEFAULT_OPEN_POSITIONS
    open_risk: float = float(DEFAULT_OPEN_RISK)
    gross: float = 1.0
    currency: float = 0.6
    lot: float = 1000.0


def pip(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


# ------------------------------------------------------------------------------ data
def load(d: Path, pairs: tuple[str, ...]) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    out = {}
    for p in pairs:
        f5, f1 = d / f"{p}_M5.csv", d / f"{p}_H1.csv"
        if not f5.exists():
            continue
        m5 = pd.read_csv(f5, index_col=0)
        m5.index = pd.to_datetime(m5.index, utc=True)
        h1 = pd.read_csv(f1, index_col=0)
        h1.index = pd.to_datetime(h1.index, utc=True)
        out[p] = (m5.sort_index(), h1.sort_index())
    return out


def signals(m5: pd.DataFrame, h1: pd.DataFrame, r: Rules) -> pd.DataFrame:
    """Per M5 bar, known at its close: dir (+1/-1/0), target, atr, and whether the bar is
    inside the trading night (for managing open positions)."""
    c = m5["close"]
    bb = bollinger(c, r.bb_n, r.bb_k)
    f = pd.DataFrame(index=m5.index)
    f["mid"], f["atr"], f["rsi"] = bb["mid"], atr(m5, r.atr_n), rsi(c, r.rsi_n)
    hadx = adx(h1, 14)
    hadx.index = hadx.index + pd.Timedelta(hours=1)          # usable once the bar closed
    hadx = hadx[~hadx.index.duplicated(keep="last")]
    f["adx_h1"] = hadx.reindex(m5.index, method="ffill")
    close_ny = (m5.index + pd.Timedelta(minutes=5)).tz_convert(NY)
    clock = np.asarray(close_ny.hour) * 60 + np.asarray(close_ny.minute)
    wd = np.asarray(close_ny.day_of_week)
    weeknight = wd <= 3                                       # Monday..Thursday evenings
    start = r.start.hour * 60 + r.start.minute
    end = r.end.hour * 60 + r.end.minute
    flat = r.flat.hour * 60 + r.flat.minute
    entry_ok = weeknight & (clock >= start) & (clock < end)
    f["night"] = weeknight & (clock >= start) & (clock <= flat)
    f["flat_now"] = clock >= flat
    spread = m5["spread_mean"]
    room_long = (f["mid"] - c) - spread
    room_short = (c - f["mid"]) - spread
    quiet = f["adx_h1"] < r.adx_max
    long_ = entry_ok & quiet & (c < bb["lower"]) & (f["rsi"] < r.rsi_low) & \
        (room_long >= r.min_reward_atr * f["atr"])
    short = entry_ok & quiet & (c > bb["upper"]) & (f["rsi"] > r.rsi_high) & \
        (room_short >= r.min_reward_atr * f["atr"])
    f["dir"] = np.where(long_, 1, np.where(short, -1, 0))
    f["skipped_room"] = entry_ok & quiet & (
        ((c < bb["lower"]) & (f["rsi"] < r.rsi_low) & ~(room_long >= r.min_reward_atr * f["atr"]))
        | ((c > bb["upper"]) & (f["rsi"] > r.rsi_high)
           & ~(room_short >= r.min_reward_atr * f["atr"])))
    return f


# ------------------------------------------------------------------------- simulator
@dataclass
class Pos:
    pair: str
    dir: int
    entry: float
    units: float
    stop: float
    target: float
    opened: pd.Timestamp
    risk_usd: float
    costs_usd: float = 0.0
    bars: int = 0


@dataclass
class Book:
    equity: float = EQUITY
    peak: float = EQUITY
    day: Any = None
    day_start: float = EQUITY
    week: Any = None
    week_start: float = EQUITY
    halted: str | None = None
    open: dict[str, Pos] = field(default_factory=dict)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)


class Night:
    def __init__(self, data: dict[str, tuple[pd.DataFrame, pd.DataFrame]], r: Rules,
                 risk: Risk | None = None, pairs: tuple[str, ...] = PAIRS) -> None:
        self.r, self.risk = r, risk or Risk()
        self.pairs = tuple(p for p in pairs if p in data)
        self.rows: dict[str, dict[pd.Timestamp, dict[str, Any]]] = {}
        times: set[pd.Timestamp] = set()
        for p in self.pairs:
            m5, h1 = data[p]
            f = signals(m5, h1, r)
            keep = f["night"].to_numpy()
            df = m5.loc[keep, ["open", "high", "low", "close", "ask_open", "ask_high",
                               "ask_low", "ask_close", "spread_mean"]].join(
                f.loc[keep, ["dir", "mid", "atr", "flat_now"]])
            self.rows[p] = dict(zip(df.index, df.to_dict("records"), strict=True))
            times.update(df.index)
            self.skipped_room = getattr(self, "skipped_room", 0) + int(f["skipped_room"].sum())
        self.times = sorted(times)
        self.rate = {}
        for c in CONVERSION:          # the bid close of each conversion pair at every step
            if c in data:
                s = data[c][0]["close"]
                self.rate[c] = s.reindex(pd.DatetimeIndex(self.times), method="ffill")
        self.book = Book()

    # --------------------------------------------------------------- conversions
    def _px(self, pair: str, t: pd.Timestamp) -> float:
        v = self.rate[pair].get(t)
        if v is None or not math.isfinite(v):
            raise KeyError(f"no {pair} rate at {t}")
        return float(v)

    def quote_usd(self, pair: str, t: pd.Timestamp) -> float:
        q = pair[3:]
        return {"USD": lambda: 1.0, "CHF": lambda: 1 / self._px("USDCHF", t),
                "CAD": lambda: 1 / self._px("USDCAD", t), "GBP": lambda: self._px("GBPUSD", t),
                "AUD": lambda: self._px("AUDUSD", t), "NZD": lambda: self._px("NZDUSD", t),
                "EUR": lambda: self._px("EURUSD", t)}[q]()

    def base_usd(self, pair: str, t: pd.Timestamp) -> float:
        b = pair[:3]
        return {"USD": lambda: 1.0, "EUR": lambda: self._px("EURUSD", t),
                "GBP": lambda: self._px("GBPUSD", t), "AUD": lambda: self._px("AUDUSD", t),
                "NZD": lambda: self._px("NZDUSD", t)}[b]()

    # ------------------------------------------------------------------- helpers
    def _reject(self, why: str) -> None:
        self.book.rejections[why] = self.book.rejections.get(why, 0) + 1

    def _extra(self, row: dict[str, Any]) -> float:
        """Additional adverse price per fill under the cost stress (more spread)."""
        return (self.r.cost_mult - 1) * float(row["spread_mean"])

    def _close(self, pos: Pos, t: pd.Timestamp, px: float, reason: str) -> None:
        pnl = (px - pos.entry) * pos.dir * pos.units * self.quote_usd(pos.pair, t)
        net = pnl - pos.costs_usd
        self.book.equity += net
        self.book.trades.append({
            "pair": pos.pair, "dir": pos.dir, "strategy": "night_scalper", "regime": "night",
            "opened": pos.opened, "closed": t, "entry": pos.entry, "exit": px,
            "reason": reason, "bars": pos.bars, "units": pos.units, "pnl_usd": net,
            "costs_usd": pos.costs_usd, "carry_usd": 0.0, "risk_usd": pos.risk_usd,
            "r": net / pos.risk_usd if pos.risk_usd > 0 else 0.0})
        del self.book.open[pos.pair]

    def _marked(self, t: pd.Timestamp, last: dict[str, dict[str, Any]]) -> float:
        eq = self.book.equity
        for p in self.book.open.values():
            row = last.get(p.pair)
            if row is None:
                continue
            exit_px = row["close"] if p.dir > 0 else row["ask_close"]
            eq += (exit_px - p.entry) * p.dir * p.units * self.quote_usd(p.pair, t) - \
                p.costs_usd
        return eq

    # ---------------------------------------------------------------------- run
    def run(self) -> Book:
        b, r = self.book, self.r
        last: dict[str, dict[str, Any]] = {}
        for t in self.times:
            ny = (t + pd.Timedelta(minutes=5)).tz_convert(NY)
            day, week = ny.date(), (ny.isocalendar().year, ny.isocalendar().week)
            prev_eq = b.curve[-1][1] if b.curve else b.equity
            if b.day != day:
                b.day, b.day_start = day, prev_eq
            if b.week != week:
                b.week, b.week_start = week, prev_eq
            for p in self.pairs:
                row = self.rows[p].get(t)
                if row is None:
                    continue
                last[p] = row
                self._fill(p, t, row)
                if p in b.open:
                    self._manage(b.open[p], t, row)
            eq = self._marked(t, last)
            b.peak = max(b.peak, eq)
            b.curve.append((t, eq))
            if b.halted is None and eq <= b.peak * (1 - self.risk.drawdown):
                b.halted = f"drawdown {self.risk.drawdown:.0%} at {t}"
                for pos in list(b.open.values()):
                    row = last[pos.pair]
                    px = row["close"] - r.slip_pips * pip(pos.pair) if pos.dir > 0 else \
                        row["ask_close"] + r.slip_pips * pip(pos.pair)
                    self._close(pos, t, px, "emergency")
                b.pending.clear()
            for p in self.pairs:
                row = self.rows[p].get(t)
                if row is not None and int(row["dir"]) != 0:
                    self._consider(p, t, row, eq)
        if self.times:
            t = self.times[-1]
            for pos in list(b.open.values()):
                row = last[pos.pair]
                self._close(pos, t, row["close"] if pos.dir > 0 else row["ask_close"], "end")
        return b

    def _fill(self, p: str, t: pd.Timestamp, row: dict[str, Any]) -> None:
        pend = self.book.pending.pop(p, None)
        if pend is None or p in self.book.open or self.book.halted:
            return
        d = pend["dir"]
        fill = (row["ask_open"] + self._extra(row)) if d > 0 else (row["open"] - self._extra(row))
        if (pend["target"] - fill) * d <= 0:
            self._reject("target passed before the fill")     # an EA cannot set this TP
            return
        stop = fill - d * pend["stop_dist"]
        risk_usd = pend["stop_dist"] * pend["units"] * self.quote_usd(p, t)
        pos = Pos(p, d, fill, pend["units"], stop, pend["target"], t, risk_usd)
        pos.costs_usd = self.r.commission_per_100k * self.r.cost_mult * pend["units"] / 100_000
        self.book.open[p] = pos

    def _manage(self, pos: Pos, t: pd.Timestamp, row: dict[str, Any]) -> None:
        r, d = self.r, pos.dir
        slip = r.slip_pips * pip(pos.pair) * r.cost_mult
        extra = self._extra(row)
        if d > 0:                                   # a long closes on the bid
            o, h, lo, c = row["open"], row["high"], row["low"], row["close"]
        else:                                       # a short closes on the ask
            o, h, lo, c = row["ask_open"], row["ask_high"], row["ask_low"], row["ask_close"]
        first_bar = pos.opened == t
        if not first_bar and ((d > 0 and o <= pos.stop) or (d < 0 and o >= pos.stop)):
            self._close(pos, t, o - d * (slip + extra), "stop_gap")
            return
        if (d > 0 and lo <= pos.stop) or (d < 0 and h >= pos.stop):      # stop first
            self._close(pos, t, pos.stop - d * (slip + extra), "stop")
            return
        if (d > 0 and h >= pos.target) or (d < 0 and lo <= pos.target):
            self._close(pos, t, pos.target - d * extra, "target")
            return
        pos.bars += 1
        if pos.bars >= r.max_bars or bool(row["flat_now"]):
            self._close(pos, t, c - d * (slip + extra), "time")

    def _consider(self, p: str, t: pd.Timestamp, row: dict[str, Any], eq: float) -> None:
        b, k = self.book, self.risk
        why = None
        if b.halted:
            why = "halted (drawdown limit)"
        elif eq <= b.day_start * (1 - k.daily):
            why = "daily loss limit"
        elif eq <= b.week_start * (1 - k.weekly):
            why = "weekly loss limit"
        elif p in b.open or p in b.pending:
            why = "already in this pair"
        elif len(b.open) + len(b.pending) >= k.max_open:
            why = "max open positions"
        if why:
            self._reject(why)
            return
        d = int(row["dir"])
        stop_dist = self.r.stop_atr * float(row["atr"])
        if not stop_dist > 0:
            self._reject("no ATR")
            return
        risk_usd = eq * k.per_trade
        per_unit = stop_dist * self.quote_usd(p, t)
        units = risk_usd / per_unit
        base_usd = self.base_usd(p, t)
        gross, ccy = 0.0, dict[str, float]()
        legs = [(x.pair, x.dir, x.units) for x in b.open.values()] + \
            [(q, v["dir"], v["units"]) for q, v in b.pending.items()]
        open_risk = sum(x.risk_usd for x in b.open.values()) + \
            sum(v["risk_usd"] for v in b.pending.values())
        if open_risk + risk_usd > k.open_risk * eq + 1e-9:
            self._reject("aggregate open risk limit")
            return
        for q, dd, u in legs:
            n = u * self.base_usd(q, t)
            gross += n
            ccy[q[:3]] = ccy.get(q[:3], 0.0) + dd * n
            ccy[q[3:]] = ccy.get(q[3:], 0.0) - dd * n
        cap = k.gross * eq - gross
        for cur, sign in ((p[:3], d), (p[3:], -d)):
            cap = min(cap, k.currency * eq - sign * ccy.get(cur, 0.0))
        if cap <= 0:
            self._reject("exposure limit")
            return
        units = min(units, cap / base_usd)
        units = math.floor(units / k.lot) * k.lot
        if units <= 0:
            self._reject("below the minimum lot")
            return
        b.pending[p] = {"dir": d, "units": units, "stop_dist": stop_dist,
                        "target": float(row["mid"]),
                        "risk_usd": stop_dist * units * self.quote_usd(p, t)}


# --------------------------------------------------------------------- evaluation
def half_years(curve: list[tuple[pd.Timestamp, float]]) -> dict[str, float]:
    if not curve:
        return {}
    s = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([t for t, _ in curve]))
    s = s[~s.index.duplicated(keep="last")]
    ends = s.groupby([s.index.year, (s.index.month > 6).astype(int)]).last()
    prev, out = EQUITY, {}
    for (y, h), v in ends.items():
        out[f"{y}-H{h + 1}"] = float(v / prev - 1)
        prev = v
    return out


def evaluate(data: dict[str, tuple[pd.DataFrame, pd.DataFrame]], r: Rules) -> dict[str, Any]:
    sim = Night(data, r)
    book = sim.run()
    res = {"trades": book.trades, "curve": book.curve, "rejections": book.rejections,
           "halted": book.halted}
    m = nqs1_bt.metrics(res)
    m["rejections"]["reward too small after the spread (at signal)"] = sim.skipped_room
    tr = book.trades
    if tr:
        pnl = np.array([x["pnl_usd"] for x in tr])
        m["avg_win_usd"] = float(pnl[pnl > 0].mean()) if (pnl > 0).any() else None
        m["avg_loss_usd"] = float(pnl[pnl < 0].mean()) if (pnl < 0).any() else None
        hours: dict[str, list[float]] = {}
        for x in tr:
            hours.setdefault(str(x["opened"].tz_convert(NY).hour), []).append(x["r"])
        m["by_ny_hour"] = {h: {"trades": len(v), "expectancy_r": float(np.mean(v))}
                           for h, v in sorted(hours.items())}
        mid = book.curve[len(book.curve) // 2][0]
        first = [x["r"] for x in tr if x["opened"] < mid]
        second = [x["r"] for x in tr if x["opened"] >= mid]
        m["halves"] = {"split": str(mid),
                       "first": {"trades": len(first),
                                 "expectancy_r": float(np.mean(first)) if first else None},
                       "second": {"trades": len(second),
                                  "expectancy_r": float(np.mean(second)) if second else None}}
    m["half_years"] = half_years(book.curve)
    return {"metrics": m, "r": [x["r"] for x in tr]}


def gates(m: dict[str, Any], mc: dict[str, Any], stress: dict[str, Any],
          sig: bool) -> dict[str, bool]:
    hy = list(m.get("half_years", {}).values())
    return {"at_least_100_trades": m["trades"] >= 100,
            "net_positive_after_all_costs": (m.get("net_return") or 0) > 0,
            "profit_factor_ge_1_2": (m.get("profit_factor") or 0) >= 1.2,
            "sharpe_ge_0_5": (m.get("sharpe") or 0) >= 0.5,
            "max_drawdown_within_5pct": m.get("halted") is None and
                (m.get("max_drawdown") or 0) > -0.05,
            "mc_prob_drawdown_over_5pct_le_25pct":
                mc.get("prob_drawdown_over_5pct", 1.0) <= 0.25,
            "positive_in_60pct_of_half_years": bool(hy) and
                float(np.mean([x > 0 for x in hy])) >= 0.6,
            "positive_at_2x_costs": (stress.get("net_return") or 0) > 0,
            "significant_after_holm": sig}


# One setting at a time at -20% / +20% (RSI: the distance from 50, 20 points, x0.8 / x1.2).
SENSITIVITY: tuple[tuple[str, dict[str, float]], ...] = (
    ("bb_k=1.6", {"bb_k": 1.6}), ("bb_k=2.4", {"bb_k": 2.4}),
    ("stop_atr=1.6", {"stop_atr": 1.6}), ("stop_atr=2.4", {"stop_atr": 2.4}),
    ("rsi=34/66", {"rsi_low": 34.0, "rsi_high": 66.0}),
    ("rsi=26/74", {"rsi_low": 26.0, "rsi_high": 74.0}),
    ("adx_max=20", {"adx_max": 20.0}), ("adx_max=30", {"adx_max": 30.0}))


def account_test(data: dict[str, tuple[pd.DataFrame, pd.DataFrame]], base: Rules
                 ) -> dict[str, Any]:
    main = evaluate(data, base)
    m = main["metrics"]
    r = np.array(main["r"])
    p = stationary_bootstrap_pvalue(r, simulations=5000, block=5) if len(r) >= 10 else None
    mc = nqs1_bt.monte_carlo(r)
    stress = evaluate(data, replace(base, cost_mult=2.0))["metrics"]
    sens: dict[str, Any] = {}
    for label, change in SENSITIVITY:
        sm = evaluate(data, replace(base, **change))["metrics"]  # type: ignore[arg-type]
        sens[label] = {x: sm.get(x) for x in ("trades", "expectancy_r", "profit_factor",
                                              "net_return")}
    exps = [x["expectancy_r"] for x in sens.values() if x["expectancy_r"] is not None]
    return {"rules": {k: str(v) for k, v in asdict(base).items()},
            "metrics": m, "p_value": p, "monte_carlo": mc,
            "stress_2x_costs": {k: stress.get(k) for k in ("trades", "net_return", "sharpe",
                                                            "expectancy_r", "profit_factor",
                                                            "max_drawdown")},
            "sensitivity": sens,
            "sensitivity_share_positive": float(np.mean([e > 0 for e in exps])) if exps
            else None}


def render(rep: dict[str, Any]) -> str:
    pct = nqs1_bt._pct
    out = ["# Night scalper: test report", "",
           f"Generated {rep['generated_at']} (harness commit `{rep.get('harness_commit')}`). "
           f"{rep['kind']}. Simulation equity {EQUITY:.0f} USD. Pre-registration: "
           f"`{PREREG}`.", ""]
    for acct, t in rep["accounts"].items():
        m = t["metrics"]
        out += [f"## {acct} account ({m.get('start', '—')[:10]} to {m.get('end', '—')[:10]})",
                "", f"**Verdict: {'PASSED' if t['passed'] else 'FAILED'}**", "",
                "| Gate | Result |", "|---|---|"]
        out += [f"| {g.replace('_', ' ')} | {'pass' if ok else 'FAIL'} |"
                for g, ok in t["gates"].items()]
        out += ["", f"* Trades {m['trades']}, win rate {nqs1_bt._rate(m.get('win_rate'))}, "
                    f"expectancy {nqs1_bt._num(m.get('expectancy_r'))} R, profit factor "
                    f"{nqs1_bt._num(m.get('profit_factor'))}",
                f"* Average win {nqs1_bt._num(m.get('avg_win_usd'))} USD, average loss "
                f"{nqs1_bt._num(m.get('avg_loss_usd'))} USD",
                f"* Net {pct(m.get('net_return'))}, Sharpe {nqs1_bt._num(m.get('sharpe'))}, "
                f"max drawdown {pct(m.get('max_drawdown'))}, p-value "
                f"{nqs1_bt._num(t.get('p_value'), 3)}",
                f"* At 2x costs: net {pct(t['stress_2x_costs'].get('net_return'))}, "
                f"expectancy {nqs1_bt._num(t['stress_2x_costs'].get('expectancy_r'))} R",
                f"* Exits: {m.get('exits')}", "",
                "| Pair | Trades | Expectancy R | Win rate |", "|---|---|---|---|"]
        out += [f"| {k} | {v['trades']} | {nqs1_bt._num(v['expectancy_r'])} | "
                f"{nqs1_bt._rate(v['win_rate'])} |" for k, v in sorted(
                    m.get("by_pair", {}).items())]
        out += ["", "| Setting | Trades | Expectancy R | Net |", "|---|---|---|---|"]
        out += [f"| {k} | {v['trades']} | {nqs1_bt._num(v['expectancy_r'])} | "
                f"{pct(v['net_return'])} |" for k, v in t["sensitivity"].items()]
    out += ["", "Historical simulation only: no orders were placed, and past results do not "
                "guarantee future results.", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--standard", type=Path, required=True)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    want = tuple(dict.fromkeys(PAIRS + CONVERSION))
    accounts = {"Standard": (a.standard, Rules()),
                "Raw Spread": (a.raw, Rules(commission_per_100k=7.0))}
    rep: dict[str, Any] = {"accounts": {}}
    for name, (d, rules) in accounts.items():
        data = load(d, want)
        missing = [p for p in want if p not in data]
        if missing:
            raise SystemExit(f"{name}: missing bars for {missing}")
        rep["accounts"][name] = account_test(data, rules)
        print(f"{name}: done", flush=True)
    flags = nqs1_bt.holm_flags({k: v["p_value"] for k, v in rep["accounts"].items()})
    for k, v in rep["accounts"].items():
        v["gates"] = gates(v["metrics"], v["monte_carlo"], v["stress_2x_costs"], flags[k])
        v["passed"] = all(v["gates"].values())
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               preregistration=PREREG, pairs=list(PAIRS))
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    a.out.with_suffix(".md").write_text(render(json.loads(json.dumps(rep, default=str))))
    print(json.dumps({k: {"passed": v["passed"],
                          "failed": [g for g, ok in v["gates"].items() if not ok]}
                      for k, v in rep["accounts"].items()}))


if __name__ == "__main__":
    main()
