"""Ensemble Donchian trend following, after Zarattini, Pagani and Barbon, "Catching Crypto
Trends" (Swiss Finance Institute RP 25-80, 2025), section 4. Spot, long-only.

Pre-registration: docs/research/2026-11-23-crypto-trends-preregistration.md (rules,
deviations, gates). Everything here is causal: day t's decision uses closes up to t and
is filled at day t+1's open.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)
TARGET_VOL = 0.25
VOL_WINDOW = 90
YEAR_DAYS = 365


# ---------------------------------------------------------------- signals
def donchian_position(close: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """Long/flat position and the trailing stop for one lookback ``n``.

    Enter when close == max of the last n closes (today included). Exit when close <=
    trailing stop. The stop starts at the channel midpoint on the entry day, then becomes
    max(previous stop, today's midpoint): it never moves down."""
    c = close.to_numpy(dtype=float)
    up = close.rolling(n, min_periods=n).max().to_numpy()
    down = close.rolling(n, min_periods=n).min().to_numpy()
    mid = 0.5 * (up + down)
    pos = np.zeros(len(c))
    stop = np.full(len(c), np.nan)
    held, s = 0.0, np.nan
    for t in range(len(c)):
        if np.isnan(up[t]):
            pos[t] = 0.0
            continue
        if held:
            s = max(s, mid[t])                 # trail upward only
            if c[t] <= s:
                held, s = 0.0, np.nan
        elif c[t] >= up[t]:                     # close equals the n-day high
            held, s = 1.0, mid[t]
        pos[t], stop[t] = held, s
    return pd.Series(pos, index=close.index), pd.Series(stop, index=close.index)


def annual_vol(close: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    return close.pct_change().rolling(window, min_periods=window).std() * math.sqrt(YEAR_DAYS)


def combo_weights(close: pd.Series, *, lookbacks: tuple[int, ...] = LOOKBACKS,
                  target_vol: float = TARGET_VOL, cap: float = 1.0) -> pd.DataFrame:
    """Per-day combo weight (mean of the lookback weights) and whether any lookback's
    signal changed that day (signal-driven trades bypass the rebalance threshold)."""
    vol = annual_vol(close)
    size = (target_vol / vol).clip(upper=cap)
    weights, changed = [], pd.Series(False, index=close.index)
    for n in lookbacks:
        pos, _ = donchian_position(close, n)
        weights.append((size * pos).fillna(0.0))
        changed |= pos.diff().fillna(0.0) != 0
    w = pd.concat(weights, axis=1).mean(axis=1)
    return pd.DataFrame({"weight": w, "signal_changed": changed, "vol": vol})


# ---------------------------------------------------------------- portfolio simulation
@dataclass
class SimConfig:
    fee: float = 0.0010
    slippage: float = 0.0005
    threshold: float = 0.20          # vol-driven change executed only if |dw| > 20% of target
    lookbacks: tuple[int, ...] = LOOKBACKS
    target_vol: float = TARGET_VOL
    cap: float = 1.0


@dataclass
class SimResult:
    equity: pd.Series
    weights: pd.DataFrame            # executed weight per coin (fraction of its sleeve)
    turnover: float                  # traded notional / average equity
    costs: float
    trades: int
    extra: dict[str, Any] = field(default_factory=dict)


def simulate(bars: dict[str, pd.DataFrame], cfg: SimConfig | None = None, *,
             start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
             initial: float = 10_000.0) -> SimResult:
    """``bars[symbol]``: daily frame (UTC midnight index) with open and close.

    Equal sleeves per coin, re-split at each month end (paper section 7.2). Day t's target
    (from closes up to t) is filled at day t+1's open, with fee and slippage on the traded
    notional."""
    cfg = cfg or SimConfig()
    cost_rate = cfg.fee + cfg.slippage
    syms = sorted(bars)
    idx = sorted(set.intersection(*(set(b.index) for b in bars.values())))
    index = pd.DatetimeIndex(idx)
    sig = {s: combo_weights(bars[s]["close"], lookbacks=cfg.lookbacks,
                            target_vol=cfg.target_vol, cap=cfg.cap).reindex(index)
           for s in syms}
    opens = {s: bars[s]["open"].reindex(index).to_numpy(float) for s in syms}
    closes = {s: bars[s]["close"].reindex(index).to_numpy(float) for s in syms}
    lo = 0 if start is None else index.searchsorted(start)
    hi = len(index) if end is None else index.searchsorted(end)
    sleeve_cash = {s: initial / len(syms) for s in syms}
    units = dict.fromkeys(syms, 0.0)
    eq_vals, w_rows, traded, costs, trades = [], [], 0.0, 0.0, 0
    for t in range(lo, hi):
        # 1) Fill yesterday's decision at today's open.
        if t > lo:
            for s in syms:
                row = sig[s].iloc[t - 1]
                px = opens[s][t]
                sleeve = sleeve_cash[s] + units[s] * px
                cur = units[s] * px / sleeve if sleeve > 0 else 0.0
                target = float(row["weight"]) if not np.isnan(row["weight"]) else 0.0
                do = bool(row["signal_changed"]) or (
                    target > 0 and abs(target - cur) > cfg.threshold * target) or (
                    target == 0 and cur > 0)
                if do and abs(target - cur) > 1e-12:
                    dn = (target - cur) * sleeve          # notional to trade
                    fee = abs(dn) * cost_rate
                    units[s] += dn / px
                    sleeve_cash[s] -= dn + fee
                    traded += abs(dn)
                    costs += fee
                    trades += 1
        # 2) Mark at today's close; re-split sleeves at month end.
        values = {s: sleeve_cash[s] + units[s] * closes[s][t] for s in syms}
        total = sum(values.values())
        eq_vals.append(total)
        w_rows.append({s: units[s] * closes[s][t] / values[s] if values[s] > 0 else 0.0
                       for s in syms})
        nxt = index[t + 1] if t + 1 < len(index) else None
        if nxt is not None and nxt.month != index[t].month:
            for s in syms:   # move cash so each sleeve holds total / B (no trade needed)
                sleeve_cash[s] += total / len(syms) - values[s]
    equity = pd.Series(eq_vals, index=index[lo:hi], name="equity")
    avg_eq = float(equity.mean()) if len(equity) else 1.0
    return SimResult(equity, pd.DataFrame(w_rows, index=index[lo:hi]), traded / avg_eq,
                     costs, trades)


# ---------------------------------------------------------------- metrics
def sharpe(equity: pd.Series) -> float | None:
    r = equity.pct_change().dropna()
    if len(r) < 30 or r.std() == 0:
        return None
    return float(r.mean() / r.std() * math.sqrt(YEAR_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    return float((1 - equity / equity.cummax()).max()) if len(equity) else 0.0


def cagr(equity: pd.Series) -> float | None:
    if len(equity) < 2:
        return None
    years = (equity.index[-1] - equity.index[0]).days / YEAR_DAYS
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else None


def calendar_years(equity: pd.Series) -> dict[int, float]:
    """Returns of FULL calendar years only."""
    out = {}
    for y, g in equity.groupby(equity.index.year):
        if g.index[0].month == 1 and g.index[0].day == 1 and g.index[-1].month == 12 \
                and g.index[-1].day == 31:
            prev = equity[equity.index < g.index[0]]
            base = prev.iloc[-1] if len(prev) else g.iloc[0]
            out[int(y)] = float(g.iloc[-1] / base - 1)
    return out


def buy_and_hold(bars: dict[str, pd.DataFrame], index: pd.DatetimeIndex) -> pd.Series:
    """Equal-weight buy-and-hold, re-split monthly like the strategy, no costs."""
    rets = pd.concat([bars[s]["close"].reindex(index).pct_change() for s in sorted(bars)],
                     axis=1).fillna(0.0)
    return (1 + rets.mean(axis=1)).cumprod() * 10_000.0


# ---------------------------------------------------------------- data
ARCHIVE = "https://data.binance.vision/data/spot"


def load_archive_dir(directory: Path, symbol: str) -> pd.DataFrame:
    """Daily klines from Binance archive zips in ``directory`` (monthly and daily files);
    duplicates resolved by open time. Timestamps in ms or us."""
    rows: dict[datetime, tuple[float, float, float, float, float]] = {}
    for z in sorted(directory.glob(f"{symbol}-1d-*.zip")):
        with zipfile.ZipFile(z) as zf:
            text = zf.read(zf.namelist()[0]).decode()
        for r in csv.reader(io.StringIO(text)):
            if not r or not r[0][:1].isdigit():
                continue
            ts = int(r[0])
            ts = ts // 1000 if ts > 10**14 else ts
            t = datetime.fromtimestamp(ts / 1000, tz=UTC)
            rows[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
    df = pd.DataFrame.from_dict(rows, orient="index",
                                columns=["open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(df.index)
    df = df.sort_index()
    full = pd.date_range(df.index[0], df.index[-1], freq="1D", tz="UTC")
    if len(full) != len(df):
        missing = full.difference(df.index)
        raise ValueError(f"{symbol}: {len(missing)} missing days, e.g. {list(missing[:3])}")
    return df


def archive_urls(symbol: str, first_month: date, today: date) -> list[str]:
    """Monthly files up to last month, daily files for the current month."""
    urls = []
    m = first_month.replace(day=1)
    this_month = today.replace(day=1)
    while m < this_month:
        urls.append(f"{ARCHIVE}/monthly/klines/{symbol}/1d/{symbol}-1d-{m:%Y-%m}.zip")
        m = (m + timedelta(days=32)).replace(day=1)
    d = this_month
    while d < today:
        urls.append(f"{ARCHIVE}/daily/klines/{symbol}/1d/{symbol}-1d-{d:%Y-%m-%d}.zip")
        d += timedelta(days=1)
    return urls
