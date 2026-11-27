"""NQS-1 engine: regime-based strategy selection, multi-confirmation entries, the cost and
expected-value checks, NEXUS risk sizing and limits, and exits.

``prepare`` builds one pair's decision frame (features, higher-timeframe view, regime,
every setup). ``Portfolio`` steps bar by bar across pairs with one shared book:
* entries are decided at a bar's close and filled at the next bar's open;
* stops and targets are checked inside later bars, with the stop assumed first when
  both are reachable in the same bar;
* the round-trip cost (spread, commission, slippage on both fills) is charged once per
  trade, and price levels are the bar prices themselves;
* carry is charged at every 21:00 UTC rollover a position is held through (Wednesday
  counts three days), from the pair's rate differential less a broker markup.

The same stepping can drive a paper runner. Nothing here places a real order.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from nexus_strategies.nqs1 import features as ft
from nexus_strategies.nqs1 import strategies as st
from nexus_strategies.nqs1.config import NQS1Config, Profile
from nexus_strategies.nqs1.regime import SELECTION, classify

Row = dict[str, Any]

HTF_COLS = ("close", "ema50", "ema200", "adx")


def prepare(signal_bars: pd.DataFrame, trend_bars: pd.DataFrame, profile: Profile,
            cfg: NQS1Config | None = None) -> pd.DataFrame:
    """One pair's decision frame. Every column at bar t uses data up to t's close."""
    cfg = cfg or NQS1Config()
    f = ft.compute(signal_bars, cfg.ind)
    h = ft.compute(trend_bars, cfg.ind)
    f = f.join(ft.align_higher(f, h, profile.trend_tf, HTF_COLS))
    f["regime"] = classify(f, cfg.regime)
    for name, frame in (("tsmom", st.tsmom(f, profile.tsmom_lookback)), ("trend", st.trend(f)),
                        ("breakout", st.breakout(f, cfg.regime.squeeze)),
                        ("mean_reversion", st.mean_reversion(f))):
        for col in ("dir", "conf", "stop_atr", "kind"):
            f[f"{name}_{col}"] = frame[col]
    return f


def choose(f: pd.DataFrame, mode: str, cfg: NQS1Config | None = None) -> pd.DataFrame:
    """The setup to act on at each bar.

    * ``adaptive``: the regime selects the strategy. Time-series momentum agreeing adds
      a confirmation. A squeeze breakout needs 3 confirmations, everything else 2. In a
      strong trend, only trades in the trend's direction.
    * a strategy name: that strategy alone, every time it fires, with no regime filter
      (used to compare each strategy with the adaptive combination).
    """
    cfg = cfg or NQS1Config()
    n = len(f)
    out = {"dir": np.zeros(n, int), "conf": np.zeros(n, int), "stop_atr": np.zeros(n),
           "kind": np.array(["r"] * n, dtype=object), "strategy": np.array([""] * n,
                                                                         dtype=object)}
    regime = f["regime"].to_numpy()
    ts = f["tsmom_dir"].to_numpy()
    mom_sign = np.sign(f["mom"].fillna(0)).to_numpy()
    for name in st.STRATEGIES:
        d = f[f"{name}_dir"].to_numpy()
        c = f[f"{name}_conf"].to_numpy().copy()
        for i in np.flatnonzero(d != 0):
            if out["dir"][i] != 0:
                continue
            if mode == "adaptive":
                if name not in SELECTION.get(str(regime[i]), ()):
                    continue
                if (regime[i] == "STRONG_BULL" and d[i] < 0) or \
                        (regime[i] == "STRONG_BEAR" and d[i] > 0):
                    continue
                conf = int(c[i]) + int(ts[i] == d[i] or mom_sign[i] == d[i])
                need = 3 if regime[i] == "VOL_CONTRACTION" else cfg.min_confirmations
            else:
                if name != mode or regime[i] == "INSUFFICIENT_DATA":
                    continue
                conf, need = int(c[i]), cfg.min_confirmations
            if conf < need:
                continue
            out["dir"][i], out["conf"][i] = d[i], conf
            out["stop_atr"][i] = f[f"{name}_stop_atr"].iat[i]
            out["kind"][i], out["strategy"][i] = f[f"{name}_kind"].iat[i], name
    return pd.DataFrame(out, index=f.index)


# ---------------------------------------------------------------------------- book
@dataclass
class Position:
    pair: str
    dir: int
    strategy: str
    regime: str
    opened: datetime
    entry: float
    units: float
    stop: float
    init_risk: float          # price distance entry -> initial stop
    target: float | None
    kind: str
    features: dict[str, float]
    bars: int = 0
    best: float = 0.0
    breakeven: bool = False
    costs_usd: float = 0.0
    carry_usd: float = 0.0


@dataclass
class Book:
    equity: float
    peak: float = 0.0
    day_start: float = 0.0
    week_start: float = 0.0
    day: Any = None
    week: Any = None
    halted: str | None = None
    open: dict[str, Position] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    curve: list[tuple[datetime, float]] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)


def usd_value(pair: str, price_amount: float, price: float) -> float:
    """A price-unit amount per unit of base currency, in USD."""
    return price_amount if pair.endswith("USD") else price_amount / price


def notional_usd(pair: str, units: float, price: float) -> float:
    return units * price if pair.endswith("USD") else units


def rollovers(after: datetime, upto: datetime) -> int:
    """Days of carry for the 21:00 UTC rollovers in (after, upto], Monday to Friday;
    Wednesday's rollover carries the weekend (3 days)."""
    if upto <= after:
        return 0
    first = pd.Timestamp(after).normalize() + pd.Timedelta(hours=21)
    n = 0
    t = first if first > after else first + pd.Timedelta(days=1)
    while t <= upto:
        if t.weekday() < 5:
            n += 3 if t.weekday() == 2 else 1
        t += pd.Timedelta(days=1)
    return n


class Portfolio:
    """Steps pairs bar by bar with one shared book and NEXUS's limits.

    ``picks`` may carry ``target_r`` (the take-profit in R for that bar's setup) and
    ``p_win`` (an out-of-sample estimate of the setup's win rate for the expected-value
    check); otherwise the configuration's target and ``win_rates`` are used. Once the
    drawdown halt fires it stays for the rest of the run, as NEXUS's kill switch does.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], picks: dict[str, pd.DataFrame],
                 profile: Profile, cfg: NQS1Config | None = None, *, equity: float = 10_000.0,
                 carry: dict[str, pd.Series] | None = None, cost_mult: float = 1.0,
                 veto: Callable[[Position, float], bool] | None = None,
                 win_rates: dict[str, float] | None = None) -> None:
        self.rows = {k: dict(zip(df.index, df.to_dict("records"), strict=True))
                     for k, df in frames.items()}
        self.pick_rows = {k: dict(zip(df.index, df.to_dict("records"), strict=True))
                          for k, df in picks.items()}
        self.p = profile
        self.cfg = cfg or NQS1Config()
        self.book = Book(equity=equity, peak=equity, day_start=equity, week_start=equity)
        self.carry = carry or {}
        self.cost_mult = cost_mult
        self.veto = veto
        self.win_rates = win_rates or {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.bar = pd.Timedelta(ft.TF_RULE[profile.signal_tf])
        self.intraday = profile.signal_tf not in ("D1", "W1")

    # ------------------------------------------------------------------ helpers
    def _cost_price(self, pair: str, *, charged: bool = False) -> float:
        """Round-trip cost in price units: spread, commission, slippage on both fills.
        ``cost_mult`` scales only what is CHARGED: a cost stress models costs turning out
        worse than the bot assumed when it decided, so decisions use the assumptions."""
        c, pip = self.cfg.costs, self.cfg.costs.pip(pair)
        base = (c.spread[pair] + c.commission_rt + 2 * c.slippage) * pip
        return base * self.cost_mult if charged else base

    def _reject(self, why: str) -> None:
        self.book.rejections[why] = self.book.rejections.get(why, 0) + 1

    def _exposure(self, price: dict[str, float]) -> tuple[float, dict[str, float]]:
        """Gross notional and net notional per currency (USD) of open positions AND
        orders waiting to fill, so setups decided on the same bar share the limits."""
        gross, ccy = 0.0, dict[str, float]()
        legs = [(p.pair, p.dir, p.units, p.entry) for p in self.book.open.values()]
        legs += [(k, v["dir"], v["units"], v["price"]) for k, v in self.pending.items()]
        for pair, d, units, ref in legs:
            n = notional_usd(pair, units, price.get(pair, ref))
            gross += n
            base, quote = pair[:3], pair[3:]
            ccy[base] = ccy.get(base, 0.0) + d * n
            ccy[quote] = ccy.get(quote, 0.0) - d * n
        return gross, ccy

    def _open_risk(self) -> float:
        """USD lost if every open stop and every pending order's stop were hit."""
        risk = sum(usd_value(p.pair, max(0.0, (p.entry - p.stop) * p.dir), p.entry) * p.units
                   for p in self.book.open.values())
        return float(risk + sum(usd_value(k, v["risk"], v["price"]) * v["units"]
                                for k, v in self.pending.items()))

    def _close(self, pos: Position, t: datetime, px: float, reason: str) -> None:
        pnl = usd_value(pos.pair, (px - pos.entry) * pos.dir, px) * pos.units
        risk_usd = usd_value(pos.pair, pos.init_risk, pos.entry) * pos.units
        net = pnl - pos.costs_usd + pos.carry_usd
        self.book.equity += net
        self.book.trades.append({
            "pair": pos.pair, "dir": pos.dir, "strategy": pos.strategy, "regime": pos.regime,
            "opened": pos.opened, "closed": t, "entry": pos.entry, "exit": px,
            "reason": reason, "bars": pos.bars, "units": pos.units, "pnl_usd": net,
            "costs_usd": pos.costs_usd, "carry_usd": pos.carry_usd, "risk_usd": risk_usd,
            "r": net / risk_usd if risk_usd > 0 else 0.0, "features": pos.features})
        del self.book.open[pos.pair]

    def _marked(self) -> float:
        return self.book.curve[-1][1] if self.book.curve else self.book.equity

    # --------------------------------------------------------------------- step
    def run(self) -> Book:
        times = sorted(set().union(*[set(r) for r in self.rows.values()]))
        last_px: dict[str, float] = {}
        prev_end: datetime | None = None
        for t in times:
            self._period(t)
            for pair, rows in self.rows.items():
                row = rows.get(t)
                if row is None:
                    continue
                self._fill_pending(pair, t, row)
                if pair in self.book.open:
                    self._manage(self.book.open[pair], t, row)
                last_px[pair] = float(row["close"])
            self._rollover(prev_end, t + self.bar, last_px)
            prev_end = t + self.bar
            self._mark(t, last_px)
            for pair, rows in self.rows.items():
                row = rows.get(t)
                if row is not None:
                    self._consider(pair, t, row, self.pick_rows[pair][t], last_px)
        for pos in list(self.book.open.values()):
            self._close(pos, times[-1], last_px[pos.pair], "end")
        self._mark(times[-1], last_px)
        return self.book

    def _period(self, t: pd.Timestamp) -> None:
        """Daily and weekly loss limits measure from the marked equity at the start of
        the day (UTC) and ISO week."""
        b = self.book
        d, w = t.date(), (t.isocalendar().year, t.isocalendar().week)
        if b.day != d:
            b.day, b.day_start = d, self._marked()
        if b.week != w:
            b.week, b.week_start = w, self._marked()

    def _mark(self, t: datetime, px: dict[str, float]) -> None:
        b = self.book
        unreal = sum(usd_value(p.pair, (px.get(p.pair, p.entry) - p.entry) * p.dir,
                               px.get(p.pair, p.entry)) * p.units - p.costs_usd + p.carry_usd
                     for p in b.open.values())
        eq = b.equity + unreal
        b.peak = max(b.peak, eq)
        b.curve.append((t, eq))
        if b.halted is None and eq <= b.peak * (1 - self.cfg.risk.max_drawdown):
            b.halted = f"drawdown {self.cfg.risk.max_drawdown:.0%} at {t}"
            for pos in list(b.open.values()):      # emergency closure
                self._close(pos, t, px.get(pos.pair, pos.entry), "emergency")
            self.pending.clear()

    def _rollover(self, prev_end: datetime | None, now: datetime,
                  px: dict[str, float]) -> None:
        """Carry for the rollovers since the previous step that each open position was
        held through (never before it opened)."""
        for pos in self.book.open.values():
            rate = self.carry.get(pos.pair)
            if rate is None or rate.empty:
                continue
            start = pos.opened if prev_end is None else max(prev_end, pos.opened)
            days = rollovers(start, now)
            if not days:
                continue
            r = float(rate.asof(now)) if now >= rate.index[0] else float("nan")
            if math.isnan(r):
                continue
            daily = st.carry_per_day(r, 0.0, pos.dir)
            pos.carry_usd += days * daily * notional_usd(pos.pair, pos.units,
                                                         px.get(pos.pair, pos.entry))

    def _fill_pending(self, pair: str, t: datetime, row: Row) -> None:
        pend = self.pending.pop(pair, None)
        if pend is None or pair in self.book.open or self.book.halted:
            return
        fill = float(row["open"])
        stop = fill - pend["dir"] * pend["risk"]
        target = None if pend["kind"] == "mid" else fill + pend["dir"] * pend["target_r"] * \
            pend["risk"]
        pos = Position(pair, pend["dir"], pend["strategy"], pend["regime"], t, fill,
                       pend["units"], stop, pend["risk"], target, pend["kind"],
                       pend["features"], best=fill)
        pos.costs_usd = usd_value(pair, self._cost_price(pair, charged=True), fill) * \
            pend["units"]
        self.book.open[pair] = pos

    def _manage(self, pos: Position, t: datetime, row: Row) -> None:
        ex = self.cfg.exits
        o, h, lo, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        atr = float(row["atr"]) if not math.isnan(row["atr"]) else pos.init_risk
        pos.bars += 1
        d = pos.dir
        # Gap through the stop at the open: filled at the open.
        if pos.bars > 1 and ((d > 0 and o <= pos.stop) or (d < 0 and o >= pos.stop)):
            self._close(pos, t, o, "stop_gap")
            return
        hit_stop = lo <= pos.stop if d > 0 else h >= pos.stop
        target = pos.target if pos.kind == "r" else float(row["bb_mid"])
        hit_target = target is not None and not math.isnan(target) and \
            ((h >= target) if d > 0 else (lo <= target)) and \
            ((target - pos.entry) * d > 0)
        if hit_stop:                                    # stop assumed first
            self._close(pos, t, pos.stop, "trailing" if pos.breakeven else "stop")
            return
        if hit_target and target is not None:
            self._close(pos, t, float(target), "target")
            return
        pos.best = max(pos.best, h) if d > 0 else min(pos.best, lo)
        gain_r = (pos.best - pos.entry) * d / pos.init_risk
        if not pos.breakeven and gain_r >= ex.breakeven_r:
            pos.stop = max(pos.stop, pos.entry) if d > 0 else min(pos.stop, pos.entry)
            pos.breakeven = True
        if gain_r >= ex.trail_start_r:
            trail = pos.best - d * ex.trail_atr * atr
            pos.stop = max(pos.stop, trail) if d > 0 else min(pos.stop, trail)
        if row["regime"] == "UNSTABLE":                 # reduce risk: tighten to 1 ATR
            tight = c - d * atr
            pos.stop = max(pos.stop, tight) if d > 0 else min(pos.stop, tight)
        pick = self.pick_rows[pos.pair].get(t)
        if pick is not None and int(pick["dir"]) == -d:
            self._close(pos, t, c, "opposite_signal")
        elif pos.bars >= self.p.max_hold_bars:
            self._close(pos, t, c, "time")

    def _session_blocked(self, t: pd.Timestamp) -> bool:
        """Intraday profiles do not open trades decided after Friday 19:00 UTC, at the
        weekend, or between 20:00 and 23:59 UTC (the rollover, when spreads widen)."""
        if not self.intraday:
            return False
        d = t + self.bar                                # decision time = bar close
        return bool((d.weekday() == 4 and d.hour >= 19) or d.weekday() >= 5 or d.hour >= 20)

    def _consider(self, pair: str, t: pd.Timestamp, row: Row, pick: Row,
                  px: dict[str, float]) -> None:
        d = int(pick["dir"])
        if d == 0:
            return
        b, r, cfg = self.book, self.cfg.risk, self.cfg
        why = None
        eq = self._marked()
        if b.halted:
            why = "halted (drawdown limit)"
        elif eq <= b.day_start * (1 - r.daily_loss):
            why = "daily loss limit"
        elif eq <= b.week_start * (1 - r.weekly_loss):
            why = "weekly loss limit"
        elif pair in b.open or pair in self.pending:
            why = "already in this pair"
        elif len(b.open) + len(self.pending) >= r.max_open:
            why = "max open positions"
        elif self._session_blocked(t):
            why = "session (rollover or weekend)"
        if why:
            self._reject(why)
            return
        atr = float(row["atr"])
        risk = float(pick["stop_atr"]) * atr
        cost = self._cost_price(pair)
        spread = cfg.costs.spread[pair] * cfg.costs.pip(pair)
        if not risk > 0 or math.isnan(risk):
            self._reject("no ATR")
            return
        if spread > cfg.costs.max_spread_atr * atr:
            self._reject("spread too wide for volatility")
            return
        if cost > cfg.max_cost_r * risk:
            self._reject("costs too large for the stop")
            return
        close = float(row["close"])
        if pick["kind"] == "mid":
            reward = (float(row["bb_mid"]) - close) * d
            target_r = reward / risk
            if target_r < 1.0:
                self._reject("reward below 1R")
                return
        else:
            tr = pick.get("target_r")
            target_r = float(tr) if tr is not None and not math.isnan(tr) else \
                cfg.exits.take_profit_r
            if target_r < cfg.min_target_r:
                self._reject("reward below minimum")
                return
        pw = pick.get("p_win")
        p_win = float(pw) if pw is not None and not math.isnan(pw) else \
            self.win_rates.get(str(pick["strategy"]))
        if p_win is not None:
            ev = p_win * target_r - (1 - p_win) - cost / risk
            if ev <= 0:
                self._reject("negative expected value after costs")
                return
        risk_usd = eq * r.risk_per_trade
        per_unit = usd_value(pair, risk, close)
        units = risk_usd / per_unit if per_unit > 0 else 0.0
        gross, ccy = self._exposure(px)
        n = notional_usd(pair, units, close)
        cap = r.max_gross_exposure * eq - gross
        base, quote = pair[:3], pair[3:]
        for cur, sign in ((base, d), (quote, -d)):
            room = r.max_currency_exposure * eq - sign * ccy.get(cur, 0.0)
            cap = min(cap, room)
        if self._open_risk() + risk_usd > r.max_open_risk * eq + 1e-9:
            self._reject("aggregate open risk limit")
            return
        if cap <= 0:
            self._reject("exposure limit")
            return
        if n > cap:
            units *= cap / n
        units = math.floor(units / cfg.lot_units) * cfg.lot_units   # broker lot step
        if units <= 0:
            self._reject("below the minimum lot")
            return
        keys = ("adx", "rsi", "vol_pct", "width_pct", "mom", "macd_hist", "htf_adx")
        feats: dict[str, float] = {k: float(row[k]) for k in keys if k in row}
        feats.update(conf=float(pick["conf"]), dist_ema20=(close - float(row["ema20"])) / atr)
        probe = Position(pair, d, str(pick["strategy"]), str(row["regime"]), t, close, units,
                         close - d * risk, risk, None, str(pick["kind"]), feats)
        if self.veto is not None and self.veto(probe, target_r):
            self._reject("ML veto")
            return
        self.pending[pair] = {"dir": d, "risk": risk, "units": units, "price": close,
                              "kind": pick["kind"],
                              "target_r": target_r, "strategy": str(pick["strategy"]),
                              "regime": str(row["regime"]), "features": feats}
