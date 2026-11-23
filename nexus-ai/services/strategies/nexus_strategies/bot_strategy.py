"""NEXUS bot strategy: EMA trend + RSI + MACD + volume, with a higher-timeframe filter
(owner's request, 2026-11-23). Long-only Binance spot.

Timeframes: entries on CLOSED 5m bars; 15m confirms the trend; 1h is the higher-timeframe
filter. All three come from the same 5m series (complete groups only), so nothing uses an
unfinished bar.

Entry (all must hold on the latest closed 5m bar):
  1. trend      5m close > EMA20 > EMA50
  2. confirm    15m EMA20 > EMA50 and 15m MACD histogram > 0
  3. filter     1h close > 1h EMA50
  4. momentum   RSI(14) between 52 and 70 and rising
  5. MACD       5m MACD line > signal and the histogram rising
  6. volume     5m volume > 1.2 x its previous 20-bar mean
  7. costs      the take-profit distance is at least 3x the round-trip cost (fees plus
                slippage), so trades that cannot beat costs are skipped
Stop: entry - 1.5 ATR(14). Take profit: entry + 3 ATR (2R).
Invalidation exit: 5m close < EMA50, or a MACD cross down. Time stop: 48 bars (4 hours).

No profitability is implied: this kind of rule failed every NEXUS out-of-sample test
after costs. It runs so the pipeline can be watched end to end, in paper mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind

CODE = "bot_ema_rsi_macd"
BASE_TF = "5m"
BAR = pd.Timedelta(minutes=5)


@dataclass(frozen=True)
class BotParams:
    rsi_low: float = 52.0
    rsi_high: float = 70.0
    vol_mult: float = 1.2
    stop_atr: float = 1.5
    target_r: float = 2.0
    round_trip_cost: float = 0.003      # 0.1% fee x2 + about 0.05% slippage x2
    min_edge_x: float = 3.0
    max_hold_bars: int = 48


def _resample(df: pd.DataFrame, rule: str, n: int) -> pd.DataFrame:
    g = df.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"})
    return out[g.size() == n].dropna()


def _align(base: pd.DataFrame, higher: pd.DataFrame, period: pd.Timedelta) -> pd.DataFrame:
    """Higher-timeframe rows known at each 5m bar's close (higher bar closed by then)."""
    left = pd.DataFrame({"_t": base.index + BAR})
    right = higher.reset_index(drop=True)
    right["_t"] = higher.index + period
    merged = pd.merge_asof(left, right.sort_values("_t"), on="_t", direction="backward")
    merged.index = base.index
    return merged.drop(columns=["_t"])


def signals(m5: pd.DataFrame, p: BotParams | None = None) -> pd.DataFrame:
    """Per closed 5m bar: entry, exit (invalidation), stop, target and each check."""
    p = p or BotParams()
    c = m5["close"]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    r = ind.rsi(c, 14)
    m = ind.macd(c)
    a = ind.atr(m5, 14)
    m15 = _resample(m5, "15min", 3)
    h1 = _resample(m5, "1h", 12)
    c15 = m15["close"]
    f15 = pd.DataFrame({"ema20": ind.ema(c15, 20), "ema50": ind.ema(c15, 50),
                        "hist": ind.macd(c15)["hist"]})
    f1h = pd.DataFrame({"close": h1["close"], "ema50": ind.ema(h1["close"], 50)})
    a15 = _align(m5, f15, pd.Timedelta(minutes=15))
    a1h = _align(m5, f1h, pd.Timedelta(hours=1))
    vol_mean = m5["volume"].shift(1).rolling(20, min_periods=20).mean()
    stop = c - p.stop_atr * a
    target = c + p.target_r * (c - stop)
    chk = {
        "trend_5m": (c > e20) & (e20 > e50),
        "confirm_15m": (a15["ema20"] > a15["ema50"]) & (a15["hist"] > 0),
        "filter_1h": a1h["close"] > a1h["ema50"],
        "rsi": r.between(p.rsi_low, p.rsi_high) & (r > r.shift(1)),
        "macd": (m["macd"] > m["signal"]) & (m["hist"] > m["hist"].shift(1)),
        "volume": m5["volume"] > p.vol_mult * vol_mean,
        "beats_costs": (target - c) / c >= p.min_edge_x * p.round_trip_cost,
    }
    ok = pd.Series(True, index=m5.index)
    for v in chk.values():
        ok &= v.fillna(False)
    valid = stop.notna() & (stop < c) & a.gt(0)
    cross_down = (m["macd"] < m["signal"]) & (m["macd"].shift(1) >= m["signal"].shift(1))
    out = pd.DataFrame({"entry": ok & valid, "exit": (c < e50) | cross_down,
                        "stop": stop, "target": target, "close": c}, index=m5.index)
    for k, v in chk.items():
        out[f"chk_{k}"] = v.fillna(False).astype(bool)
    return out


def first_failed(row: pd.Series) -> str | None:
    for k in row.index:
        if k.startswith("chk_") and not bool(row[k]):
            return f"{k[4:]} not met"
    return None


def latest(sym: str, m5: pd.DataFrame, _btc: Any = None, p: BotParams | None = None
           ) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Runner adapter: a one-row regime frame for the dashboard and the candidate (if any)
    on the latest closed 5m bar. Every evaluated bar is returned as a candidate so the
    dashboard can explain why NO TRADE was taken; ``entry`` says whether it qualifies."""
    p = p or BotParams()
    sig = signals(m5, p)
    last = sig.iloc[-1]
    e = bool(last["entry"])
    trend = "up" if bool(last["chk_trend_5m"]) and bool(last["chk_filter_1h"]) else "not up"
    reg = pd.DataFrame({"regime": ["TRADEABLE" if bool(last["chk_filter_1h"]) else "FILTERED"],
                        "trend": [trend], "btc_risk_off": [False], "risk_mult": [1.0]},
                       index=[sig.index[-1]])
    stop, target, close = float(last["stop"]), float(last["target"]), float(last["close"])
    cand = {"strategy": CODE, "timeframe": BASE_TF, "bar": sig.index[-1], "entry": e,
            "candidate": e, "reason": None if e else first_failed(last),
            "stop": stop if np.isfinite(stop) else close, "r": close - stop
            if np.isfinite(stop) else 0.0, "tp1": target, "tp2": target,
            "score": float(sum(bool(last[k]) for k in last.index if k.startswith("chk_")) / 7),
            "exit": bool(last["exit"]),
            # Stored as a signal only when it qualifies or is one check short (keeps the
            # table small; the dashboard shows every evaluation from the live status).
            "record": e or sum(bool(last[k]) for k in last.index if k.startswith("chk_")) >= 6,
            **{k: bool(last[k]) for k in last.index if k.startswith("chk_")}}
    return reg, [cand]
