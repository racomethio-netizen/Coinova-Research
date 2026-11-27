"""One bracket trade, bar by bar: the same code for the historical test and for the
forward record, so the two can never disagree about what a signal would have done.

Execution model (conservative):
* Entry: a market order at the OPEN of the first execution bar after the signal bar's
  close. Forex bars are BID prices with a per-bar ``spread``: a buy pays the ask (bid +
  spread), a sell gets the bid. Both pay slippage.
* The stop is fixed at the signal. The target is ``entry + side * rr * risk`` from the
  actual fill, so the planned reward/risk is exactly ``rr``.
* A long's stop and target are checked on bid prices; a short's on ask prices
  (bid + spread). A stop that the market gaps through fills at the open (worse).
  A target fills at its price, never better (gaps in our favour are not credited).
* If a bar touches both the stop and the target, the stop is assumed first.
* Time stop after ``max_bars`` execution bars, and the optional clock exit, both at the
  next bar's open with slippage.
* Crypto (spot): a fee on both sides; stops and time exits also pay slippage; the target
  is a resting limit order (fee only).
* Forex swap: an optional ``carry`` function charges the financing of positions held over
  the 21:00 UTC rollover.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Costs:
    market: str                    # "forex" | "crypto"
    slippage: float                # forex: price units per market fill; crypto: fraction
    fee: float = 0.0               # crypto: fraction of notional per side
    multiplier: float = 1.0        # cost stress: spread, slippage and fees x this

    def stressed(self, k: float) -> Costs:
        return replace(self, multiplier=self.multiplier * k)


@dataclass(frozen=True)
class Bars:
    """An execution frame as arrays (fast loops). ``spread`` is zero for crypto."""

    time: np.ndarray               # int64 ns, bar open times
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray
    period_ns: int

    @classmethod
    def from_frame(cls, df: pd.DataFrame, period_seconds: int) -> Bars:
        idx = pd.DatetimeIndex(df.index)
        t = ((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(1, "ns")).to_numpy(np.int64)
        spread = df["spread"].to_numpy(float) if "spread" in df else np.zeros(len(df))
        o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        return cls(t, o, h, lo, c, np.nan_to_num(spread, nan=0.0), int(period_seconds * 1e9))


@dataclass
class Trade:
    symbol: str
    side: int
    signal_time: int               # ns, open time of the signal bar
    entry_time: int                # ns
    entry: float
    stop: float
    target: float
    risk: float                    # |entry - stop| in price units
    exit_time: int                 # ns: when the position is flat again
    exit: float
    reason: str                    # "stop" | "target" | "time" | "clock" | "end_of_data"
    net_r: float
    bars: int
    carry: float = 0.0             # price units, included in net_r

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        for k in ("signal_time", "entry_time", "exit_time"):
            d[k] = pd.Timestamp(d[k], tz="UTC").isoformat()
        return d


Carry = Callable[[int, int, int, float], float]   # (entry ns, exit ns, side, price) -> cost


def first_entry(bars: Bars, signal_close_ns: int, max_delay_ns: int) -> int | None:
    """Index of the first execution bar opening at or after the signal's close, if it
    opens within ``max_delay_ns`` (otherwise the signal is stale and dropped)."""
    i = int(np.searchsorted(bars.time, signal_close_ns, side="left"))
    if i >= len(bars.time) or bars.time[i] - signal_close_ns > max_delay_ns:
        return None
    return i


def _buy(px: float, spread: float, c: Costs) -> float:
    if c.market == "forex":
        return px + (spread + c.slippage) * c.multiplier
    return px * (1 + c.slippage * c.multiplier)


def _sell(px: float, c: Costs) -> float:
    if c.market == "forex":
        return px - c.slippage * c.multiplier
    return px * (1 - c.slippage * c.multiplier)


def simulate(bars: Bars, i: int, side: int, stop: float, rr: float, max_bars: int,
             costs: Costs, *, symbol: str = "", signal_time: int = 0,
             exit_at: int | None = None, carry: Carry | None = None) -> Trade | None:
    """The trade entered at bar ``i``'s open; None if the fill is already beyond the stop
    (for example a weekend gap) or the clock exit has passed."""
    m = costs.multiplier
    o, h, lo, t, spr = bars.open, bars.high, bars.low, bars.time, bars.spread
    if exit_at is not None and t[i] >= exit_at:
        return None
    entry = _buy(o[i], spr[i], costs) if side > 0 else _sell(o[i], costs)
    risk = side * (entry - stop)
    if not np.isfinite(risk) or risk <= 0:
        return None
    target = entry + side * rr * risk
    n = len(t)
    last = min(i + max_bars - 1, n - 1)
    exit_px, reason, j_exit, flat = float("nan"), "", i, 0
    for j in range(i, last + 1):
        if exit_at is not None and j > i and t[j] >= exit_at:
            exit_px = _sell(o[j], costs) if side > 0 else _buy(o[j], spr[j], costs)
            reason, j_exit, flat = "clock", j, int(t[j])
            break
        if side > 0:
            hit_stop, hit_target = lo[j] <= stop, h[j] >= target
            stop_fill = _sell(min(stop, o[j]), costs)
        else:
            add = spr[j] * m if costs.market == "forex" else 0.0      # bid -> ask
            hit_stop, hit_target = h[j] + add >= stop, lo[j] + add <= target
            worst = max(stop, o[j] + add)
            stop_fill = worst + costs.slippage * m if costs.market == "forex" \
                else worst * (1 + costs.slippage * m)
        if hit_stop:
            exit_px, reason, j_exit = stop_fill, "stop", j
            flat = int(t[j] + bars.period_ns)
            break
        if hit_target:
            exit_px, reason, j_exit = target, "target", j
            flat = int(t[j] + bars.period_ns)
            break
    else:
        if last + 1 < n:
            j = last + 1
            exit_px = _sell(o[j], costs) if side > 0 else _buy(o[j], spr[j], costs)
            reason, j_exit, flat = "time", j, int(t[j])
        else:
            exit_px, reason, j_exit = bars.close[last], "end_of_data", last
            flat = int(t[last] + bars.period_ns)
    pnl = side * (exit_px - entry)
    if costs.market == "crypto":
        pnl -= costs.fee * m * (entry + exit_px)
    fin = carry(int(t[i]), flat, side, entry) * m if carry is not None else 0.0
    pnl -= fin
    return Trade(symbol, side, signal_time, int(t[i]), float(entry), float(stop),
                 float(target), float(risk), flat, float(exit_px), reason,
                 float(pnl / risk), j_exit - i + 1, float(fin))


def candidates(bars: Bars, signals: pd.DataFrame, signal_period_s: int, max_delay_ns: int,
               costs: Costs, *, symbol: str, carry: Carry | None = None) -> list[Trade]:
    """Every signal simulated on its own (overlaps are resolved by ``book``)."""
    out: list[Trade] = []
    if signals.empty:
        return out
    idx = pd.DatetimeIndex(signals.index)
    sig_ns = ((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(1, "ns")).to_numpy(np.int64)
    close_ns = sig_ns + int(signal_period_s * 1e9)
    for k, row in enumerate(signals.itertuples(index=False)):
        i = first_entry(bars, int(close_ns[k]), max_delay_ns)
        if i is None:
            continue
        ex = None if pd.isna(row.exit_at) else int(pd.Timestamp(row.exit_at).value)
        tr = simulate(bars, i, int(row.side), float(row.stop), float(row.rr),
                      int(row.max_bars), costs, symbol=symbol, signal_time=int(sig_ns[k]),
                      exit_at=ex, carry=carry)
        if tr is not None:
            out.append(tr)
    return out


def book(trades: list[Trade], max_open: int = 3) -> list[Trade]:
    """Accept trades in entry order: one position per symbol, at most ``max_open`` at a
    time (brief section F). A position frees its slot when it is flat."""
    taken: list[Trade] = []
    open_: list[Trade] = []
    for tr in sorted(trades, key=lambda x: (x.entry_time, x.symbol, x.signal_time)):
        open_ = [o for o in open_ if o.exit_time > tr.entry_time]
        if len(open_) >= max_open or any(o.symbol == tr.symbol for o in open_):
            continue
        taken.append(tr)
        open_.append(tr)
    return taken
