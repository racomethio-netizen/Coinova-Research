"""One market-intelligence snapshot per instrument, for forex and crypto alike (unified
brief, section D). It combines the existing causal analysis:
* ``mtf`` (trend, momentum, volatility and structure votes per timeframe);
* ``technical`` (EMA, RSI, MACD, ADX, ATR, structure, liquidity patterns);
* ``levels`` (support and resistance);
* a daily-anchored VWAP;
* the FX session;
* a regime label;
* the economic-event blackout (``nexus_news.calendar``).

Everything comes from CLOSED bars that NEXUS stores; a missing input is reported as
missing (None), never estimated. These are features for validated rules, not trade
signals.

Regime label (fixed thresholds, stated here):
* HIGH_VOLATILITY: the base timeframe's ATR is above the 90th percentile of its recent
  history;
* TRENDING_UP / TRENDING_DOWN: trend votes across timeframes agree at ≥ 0.5 / ≤ -0.5;
* RANGING: anything else.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from nexus_common import sessions
from nexus_strategies import indicators as ind
from nexus_strategies.frames import TF_SECONDS

HIGH_VOL_PCT = 0.90
TREND_AGREE = 0.5
FX_DAY_SHIFT = timedelta(hours=2)   # FX day from 22:00 UTC: shift +2 h, then floor to day


def fx_session(at: datetime) -> str:
    """The FX session by UTC hour (approximate desk hours; the week's open and close
    follow ``sessions`` with New York daylight saving)."""
    if not sessions.is_open("fx_24x5", at):
        return "closed"
    h = at.hour
    if 12 <= h < 16:
        return "London / New York overlap"
    if 7 <= h < 12:
        return "London"
    if 16 <= h < 21:
        return "New York"
    return "Asia-Pacific"


def daily_vwap(df: pd.DataFrame, market: str) -> pd.Series:
    """VWAP anchored at each day's start (UTC day; the FX day from 22:00 UTC). Forex
    volume is MetaTrader tick volume."""
    if df.empty or "volume" not in df:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(df.index)
    day = (idx + FX_DAY_SHIFT).floor("D") if market == "forex" else idx.floor("D")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).groupby(day).cumsum()
    v = df["volume"].groupby(day).cumsum()
    return (pv / v.replace(0, np.nan)).rename("vwap")


def regime(trend_agreement: float | None, atr_percentile: float | None) -> str | None:
    if atr_percentile is not None and atr_percentile > HIGH_VOL_PCT:
        return "HIGH_VOLATILITY"
    if trend_agreement is None:
        return None
    if trend_agreement >= TREND_AGREE:
        return "TRENDING_UP"
    if trend_agreement <= -TREND_AGREE:
        return "TRENDING_DOWN"
    return "RANGING"


def _f(x: Any, nd: int = 4) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if np.isfinite(v) else None


def snapshot(symbol: str, market: str, frames: dict[str, pd.DataFrame], base_tf: str,
             now: datetime, *, spread: float | None = None,
             event_risk: dict[str, Any] | None = None) -> dict[str, Any]:
    from nexus_analysis.levels import support_resistance
    from nexus_analysis.mtf import mtf_snapshot
    from nexus_analysis.technical import technical_snapshot

    frames = {tf: (f if "volume" in f else f.assign(volume=np.nan))   # missing, not zero
              for tf, f in frames.items() if f is not None and not f.empty}
    base = frames.get(base_tf)
    out: dict[str, Any] = {"symbol": symbol, "market": market, "base_timeframe": base_tf,
                           "event_risk": event_risk,
                           "session": fx_session(now) if market == "forex" else "24/7"}
    if base is None or len(base) < 60:
        return out | {"insufficient_history": True,
                      "bars": 0 if base is None else len(base)}
    tech = technical_snapshot(base, frames.get("1d"))
    mtf = mtf_snapshot(frames, base_tf)
    close_t = base.index[-1] + pd.Timedelta(seconds=TF_SECONDS[base_tf])
    price = float(base["close"].iloc[-1])
    atr = ind.atr(base, 14).iloc[-1]
    sr = support_resistance(base).iloc[-1]
    vw = daily_vwap(base, market)
    vol = tech.get("volatility", {})
    scores = mtf.get("scores", {})
    liquidity: dict[str, Any] = {"relative_volume": tech.get("volume", {}).get(
        "relative_volume")}
    if market == "crypto" and "1h" in frames:
        h = frames["1h"].tail(24)
        liquidity["quote_volume_24h"] = _f((h["volume"] * h["close"]).sum(), 0)
    if market == "forex":
        liquidity["spread"] = _f(spread, 6)
        liquidity["spread_vs_atr"] = _f(spread / atr, 3) if spread and atr else None
    out |= {
        "insufficient_history": False, "bars": len(base),
        "as_of": close_t.isoformat(),
        "data_age_minutes": _f((now - close_t.to_pydatetime()).total_seconds() / 60, 1),
        "price": _f(price, 8),
        "regime": regime(scores.get("trend_agreement"), vol.get("atr_percentile")),
        "trend_by_timeframe": {tf: v.get("trend") for tf, v in mtf.get("timeframes", {}).items()},
        "scores": scores,
        "momentum": {k: tech.get("momentum", {}).get(k) for k in ("rsi", "macd_hist", "roc_12")},
        "trend_strength": {"adx": tech.get("trend", {}).get("adx"),
                           "ema_20": tech.get("trend", {}).get("ema_20"),
                           "ema_50": tech.get("trend", {}).get("ema_50"),
                           "ema_200": tech.get("trend", {}).get("ema_200")},
        "volatility": {"atr": _f(atr, 8), "atr_pct": _f(atr / price, 5),
                       "atr_percentile": vol.get("atr_percentile"),
                       "expanding": vol.get("expanding")},
        "vwap": {"value": _f(vw.iloc[-1], 8) if len(vw) else None,
                 "distance_atr": _f((price - vw.iloc[-1]) / atr, 2) if len(vw) and atr else None},
        "levels": {"support": _f(sr.get("support"), 8),
                   "support_strength": _f(sr.get("support_strength"), 0),
                   "resistance": _f(sr.get("resistance"), 8),
                   "resistance_strength": _f(sr.get("resistance_strength"), 0)},
        "structure": {k: tech.get("structure", {}).get(k)
                      for k in ("trend", "last_event", "last_event_at")},
        "liquidity": liquidity,
    }
    return out
