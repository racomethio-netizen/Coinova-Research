"""Forex modules 1 to 3 (unified brief section C). Rules are fixed in
docs/research/2026-11-27-strategy-modules-preregistration.md; every value used at a bar
comes from that bar or earlier (closed higher-timeframe bars only, ``align_higher``).

Frames: ``fx_frames`` (M5 BID bars with ``spread``, H1, and the FX day D1 from 22:00 UTC).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher
from nexus_strategies.modules.base import Frames, empty_signals, make_signals

M5 = pd.Timedelta(minutes=5)


def _minutes(ts: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(ts.hour * 60 + ts.minute)


# ----------------------------------------------------------------------------- module 1


@dataclass(frozen=True)
class FxScalpConfig:
    """Scalping momentum pullback on M5 in the direction of the H1 trend."""

    session_from: int = 7 * 60      # signal bar CLOSE in [07:00, 15:00] UTC, Monday-Friday
    session_to: int = 15 * 60
    h1_fast: int = 20
    h1_slow: int = 50
    m5_fast: int = 20
    m5_slow: int = 50
    lookback: int = 6               # bars in which the pullback must have happened
    rsi_n: int = 14
    rsi_pullback: float = 45.0      # long: RSI dipped to <= 45 (short: >= 55)
    atr_n: int = 14
    stop_buffer_atr: float = 0.2
    min_stop_atr: float = 1.0
    max_stop_atr: float = 3.0
    max_spread_of_risk: float = 0.15
    rr: float = 2.0
    max_bars: int = 36              # 3 hours of M5


def fx_scalp_signals(frames: Frames, cfg: FxScalpConfig) -> pd.DataFrame:
    m5, h1 = frames["5m"], frames["1h"]
    if len(m5) < cfg.m5_slow + cfg.lookback or len(h1) < cfg.h1_slow:
        return empty_signals()
    f = m5.copy()
    f["ef"], f["es"] = ind.ema(f["close"], cfg.m5_fast), ind.ema(f["close"], cfg.m5_slow)
    f["rsi"], f["atr"] = ind.rsi(f["close"], cfg.rsi_n), ind.atr(f, cfg.atr_n)
    h = pd.DataFrame({"c": h1["close"], "ef": ind.ema(h1["close"], cfg.h1_fast),
                      "es": ind.ema(h1["close"], cfg.h1_slow)}, index=h1.index)
    f = align_higher(f, "5m", h, "1h", "h1_")
    n = cfg.lookback
    up = (f["h1_c"] > f["h1_es"]) & (f["h1_ef"] > f["h1_es"]) & (f["ef"] > f["es"])
    dn = (f["h1_c"] < f["h1_es"]) & (f["h1_ef"] < f["h1_es"]) & (f["ef"] < f["es"])
    touched_l = (f["low"] - f["ef"]).shift(1).rolling(n, min_periods=n).min() <= 0
    touched_s = (f["high"] - f["ef"]).shift(1).rolling(n, min_periods=n).max() >= 0
    dip_l = f["rsi"].shift(1).rolling(n, min_periods=n).min() <= cfg.rsi_pullback
    dip_s = f["rsi"].shift(1).rolling(n, min_periods=n).max() >= 100 - cfg.rsi_pullback
    go_l = (f["close"] > f["ef"]) & (f["close"] > f["high"].shift(1)) & (f["rsi"] > 50)
    go_s = (f["close"] < f["ef"]) & (f["close"] < f["low"].shift(1)) & (f["rsi"] < 50)
    lo_n = f["low"].rolling(n + 1, min_periods=n + 1).min()
    hi_n = f["high"].rolling(n + 1, min_periods=n + 1).max()
    atr = f["atr"]
    dist_l = np.maximum(f["close"] - (lo_n - cfg.stop_buffer_atr * atr), cfg.min_stop_atr * atr)
    dist_s = np.maximum((hi_n + cfg.stop_buffer_atr * atr) - f["close"], cfg.min_stop_atr * atr)
    close_t = f.index + M5
    mins = _minutes(close_t)
    when = pd.Series((mins >= cfg.session_from) & (mins <= cfg.session_to)
                     & (np.asarray(close_t.day_of_week) < 5), index=f.index)
    long = up & touched_l & dip_l & go_l & when & (dist_l <= cfg.max_stop_atr * atr) \
        & (f["spread"] <= cfg.max_spread_of_risk * dist_l)
    short = dn & touched_s & dip_s & go_s & when & (dist_s <= cfg.max_stop_atr * atr) \
        & (f["spread"] <= cfg.max_spread_of_risk * dist_s)
    long, short = long.fillna(False).astype(bool), short.fillna(False).astype(bool)
    sel = long | short
    side = np.where(long, 1, -1)[sel.to_numpy()]
    stop = np.where(long, f["close"] - dist_l, f["close"] + dist_s)[sel.to_numpy()]
    return make_signals(f.index[sel.to_numpy()], side, stop, cfg.rr, cfg.max_bars)


# ----------------------------------------------------------------------------- module 2


@dataclass(frozen=True)
class FxBreakoutConfig:
    """Intraday trend breakout: the London break of the Asian range, with the D1 trend."""

    asia_from: int = 0              # Asian range: M5 bars opening in [00:00, 07:00) UTC
    asia_to: int = 7 * 60
    min_asia_bars: int = 60         # of 84
    window_from: int = 7 * 60       # breakout bar OPENS in [07:00, 11:00) UTC
    window_to: int = 11 * 60
    d1_ema: int = 50
    d1_atr: int = 14
    min_range_atr: float = 0.25     # Asian range between 0.25 and 1.0 D1 ATR
    max_range_atr: float = 1.0
    max_spread_of_risk: float = 0.15
    rr: float = 2.0
    exit_hour: int = 20             # flat at 20:00 UTC
    max_bars: int = 13 * 12         # safety net; the clock exit comes first


def fx_breakout_signals(frames: Frames, cfg: FxBreakoutConfig) -> pd.DataFrame:
    m5, d1 = frames["5m"], frames["1d"]
    if len(d1) < cfg.d1_ema or m5.empty:
        return empty_signals()
    f = m5.copy()
    d = pd.DataFrame({"c": d1["close"], "e": ind.ema(d1["close"], cfg.d1_ema),
                      "a": ind.atr(d1, cfg.d1_atr)}, index=d1.index)
    f = align_higher(f, "5m", d, "1d", "d1_")
    mins = _minutes(pd.DatetimeIndex(f.index))
    day = pd.DatetimeIndex(f.index).floor("D")
    asia = f[(mins >= cfg.asia_from) & (mins < cfg.asia_to)]
    g = asia.groupby(pd.DatetimeIndex(asia.index).floor("D"))
    rng = pd.DataFrame({"hi": g["high"].max(), "lo": g["low"].min(), "n": g["close"].count()})
    rng = rng[rng["n"] >= cfg.min_asia_bars]
    f["hi"] = rng["hi"].reindex(day).to_numpy()
    f["lo"] = rng["lo"].reindex(day).to_numpy()
    width = f["hi"] - f["lo"]
    ok_w = (width >= cfg.min_range_atr * f["d1_a"]) & (width <= cfg.max_range_atr * f["d1_a"])
    window = pd.Series((mins >= cfg.window_from) & (mins < cfg.window_to)
                       & (np.asarray(pd.DatetimeIndex(f.index).day_of_week) < 5), index=f.index)
    up_trend, dn_trend = f["d1_c"] > f["d1_e"], f["d1_c"] < f["d1_e"]
    brk_l = window & up_trend & (f["close"] > f["hi"])
    brk_s = window & dn_trend & (f["close"] < f["lo"])
    brk = (brk_l | brk_s).fillna(False).astype(bool)
    first = brk & (brk.groupby(day).cumsum() == 1)        # one attempt per pair per day
    mid = (f["hi"] + f["lo"]) / 2
    risk = (f["close"] - mid).abs()
    take = first & ok_w.fillna(False) & (f["spread"] <= cfg.max_spread_of_risk * risk)
    take = take.fillna(False).astype(bool).to_numpy()
    side = np.where(brk_l.fillna(False).to_numpy(), 1, -1)[take]
    exit_at = (day + pd.Timedelta(hours=cfg.exit_hour))[take]
    return make_signals(f.index[take], side, mid.to_numpy()[take], cfg.rr, cfg.max_bars,
                        exit_at=exit_at)


# ----------------------------------------------------------------------------- module 3


@dataclass(frozen=True)
class FxSwingConfig:
    """Swing trend following on the FX day: a 20-day breakout in the EMA50/200 trend."""

    fast: int = 50
    slow: int = 200
    breakout: int = 20
    atr_n: int = 20
    stop_atr: float = 2.0
    rr: float = 2.0
    max_days: int = 40
    exec_bars_per_day: int = 24     # H1 execution bars


def fx_swing_signals(frames: Frames, cfg: FxSwingConfig) -> pd.DataFrame:
    d1 = frames["1d"]
    if len(d1) < cfg.slow:
        return empty_signals()
    c = d1["close"]
    ef, es, atr = ind.ema(c, cfg.fast), ind.ema(c, cfg.slow), ind.atr(d1, cfg.atr_n)
    hh = d1["high"].shift(1).rolling(cfg.breakout, min_periods=cfg.breakout).max()
    ll = d1["low"].shift(1).rolling(cfg.breakout, min_periods=cfg.breakout).min()
    long = ((ef > es) & (c > hh)).fillna(False).astype(bool)
    short = ((ef < es) & (c < ll)).fillna(False).astype(bool)
    sel = (long | short).to_numpy()
    side = np.where(long, 1, -1)[sel]
    stop = (c - np.where(long, 1, -1) * cfg.stop_atr * atr).to_numpy()[sel]
    return make_signals(d1.index[sel], side, stop, cfg.rr,
                        cfg.max_days * cfg.exec_bars_per_day)
