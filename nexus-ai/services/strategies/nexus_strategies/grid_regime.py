"""Market regime for grid trading: eight classes from CLOSED 1h bars (causal: a label at
bar t uses bars up to t only).

Classes, in order of precedence:
1. ``EXTREME``: a close-to-close move larger than ``extreme_atr_mult`` x the prior ATR,
   ATR% above 3x its 30-day median, or a 24h move beyond ``extreme_24h``.
2. ``LOW_LIQUIDITY``: rolling 24h quote volume below ``min_quote_volume_24h``.
3. ``HIGH_VOLATILITY``: ATR% above 1.8x its 30-day median, or Bollinger-band width above
   2x its 30-day median (volatility expansion). Relative thresholds: a calm market is
   never flagged just because some bar is always in its top decile.
4. ``STRONG_BULL`` / ``STRONG_BEAR``: EMA20 > EMA50 > EMA200 with close above EMA50 (or
   the mirror image), and ADX >= ``adx_strong``.
5. ``WEAK_BULL`` / ``WEAK_BEAR``: the EMA20/EMA50 ordering and close vs EMA200 agree, and
   ADX >= ``adx_trend``.
6. ``SIDEWAYS``: everything else (ADX below the trend threshold or EMAs disagree).

Grid permission (what an adaptive grid may run in each class):
* spot: SIDEWAYS → long grid; WEAK_BULL → long grid; everything else → NO_TRADE.
* futures: SIDEWAYS → neutral grid; WEAK_BULL → long grid; WEAK_BEAR → short grid;
  everything else → NO_TRADE.

Start conditions on top of the class: RSI(14) within [``rsi_low``, ``rsi_high``]. When
an order book is supplied, the absolute top-of-book imbalance must be at most
``max_imbalance``. Support and resistance (48-bar Donchian) are reported for the grid
designer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind

CLASSES = ("SIDEWAYS", "WEAK_BULL", "STRONG_BULL", "WEAK_BEAR", "STRONG_BEAR",
           "HIGH_VOLATILITY", "LOW_LIQUIDITY", "EXTREME")
SPOT_GRID = {"SIDEWAYS": "long", "WEAK_BULL": "long"}
FUTURES_GRID = {"SIDEWAYS": "neutral", "WEAK_BULL": "long", "WEAK_BEAR": "short"}


@dataclass(frozen=True)
class GridRegimeConfig:
    adx_trend: float = 20.0
    adx_strong: float = 30.0
    extreme_atr_mult: float = 4.0     # one-bar move vs the prior ATR
    extreme_24h: float = 0.15
    extreme_vol_mult: float = 3.0     # ATR% vs its 30-day median
    high_vol_mult: float = 1.8        # ATR% vs its 30-day median
    bb_expansion: float = 2.0         # BB width vs its 30-day median
    rank_window: int = 720          # 30 days of hourly bars
    rank_min: int = 168
    min_quote_volume_24h: float = 20_000_000.0
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    max_imbalance: float = 0.6


def classify(df: pd.DataFrame, cfg: GridRegimeConfig | None = None) -> pd.DataFrame:
    """Label every bar of a 1h OHLCV frame (index = bar open time)."""
    cfg = cfg or GridRegimeConfig()
    c = df["close"].astype(float)
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    adx = ind.adx(df, 14)
    atr = ind.atr(df, 14)
    atr_pct = atr / c
    rank = atr_pct.rolling(cfg.rank_window, min_periods=cfg.rank_min).rank(pct=True)
    atr_med = atr_pct.rolling(cfg.rank_window, min_periods=cfg.rank_min).median()
    bbw = ind.bollinger(c, 20, 2.0)["width"]
    move = c.pct_change().abs()
    qv24 = (c * df["volume"].astype(float)).rolling(24, min_periods=24).sum()
    chg24 = c / c.shift(24) - 1
    rsi = ind.rsi(c, 14)
    bbw_med = bbw.rolling(cfg.rank_window, min_periods=cfg.rank_min).median()
    # Thresholds relative to the symbol's own 30-day median: a calm market is not flagged
    # just because some bar is always in the top decile.
    extreme = ((move > cfg.extreme_atr_mult * atr_pct.shift(1))
               | (atr_pct > cfg.extreme_vol_mult * atr_med) | (chg24.abs() > cfg.extreme_24h))
    low_liq = qv24 < cfg.min_quote_volume_24h
    high_vol = (atr_pct > cfg.high_vol_mult * atr_med) | (bbw > cfg.bb_expansion * bbw_med)
    bull_order, bear_order = e20 > e50, e20 < e50
    strong_bull = bull_order & (e50 > e200) & (c > e50) & (adx >= cfg.adx_strong)
    strong_bear = bear_order & (e50 < e200) & (c < e50) & (adx >= cfg.adx_strong)
    weak_bull = bull_order & (c > e200) & (adx >= cfg.adx_trend)
    weak_bear = bear_order & (c < e200) & (adx >= cfg.adx_trend)
    label = np.select(
        [extreme, low_liq, high_vol, strong_bull, strong_bear, weak_bull, weak_bear],
        ["EXTREME", "LOW_LIQUIDITY", "HIGH_VOLATILITY", "STRONG_BULL", "STRONG_BEAR",
         "WEAK_BULL", "WEAK_BEAR"], default="SIDEWAYS")
    out = pd.DataFrame({"regime": label, "adx": adx, "atr_pct": atr_pct, "atr_rank": rank,
                        "rsi": rsi, "ema20": e20, "ema50": e50, "ema200": e200,
                        "support": ind.donchian_low(df["low"], 48),
                        "resistance": ind.donchian_high(df["high"], 48),
                        "quote_volume_24h": qv24, "change_24h": chg24}, index=df.index)
    out.iloc[:200, out.columns.get_loc("regime")] = "WARMUP"   # EMA200 not settled yet
    return out


def grid_mode(regime: str, market: str) -> str | None:
    """Grid direction allowed in ``regime`` (None = NO_TRADE)."""
    return (SPOT_GRID if market == "spot" else FUTURES_GRID).get(regime)


def latest(df: pd.DataFrame, market: str, *, imbalance: float | None = None,
           cfg: GridRegimeConfig | None = None) -> dict[str, Any]:
    """Regime of the last closed bar with the grid decision and its reasons."""
    cfg = cfg or GridRegimeConfig()
    if len(df) < 210:
        return {"regime": "INSUFFICIENT_DATA", "grid": None, "start_ok": False,
                "reasons": [f"only {len(df)} closed hourly bars (need 210)"]}
    row = classify(df, cfg).iloc[-1]
    regime = str(row["regime"])
    mode = grid_mode(regime, market)
    reasons = [f"regime {regime}: " + (f"{mode} grid allowed" if mode else "NO_TRADE")]
    start_ok = mode is not None
    rsi = float(row["rsi"])
    if not cfg.rsi_low <= rsi <= cfg.rsi_high:
        start_ok = False
        reasons.append(f"RSI {rsi:.1f} outside [{cfg.rsi_low:g}, {cfg.rsi_high:g}]: wait")
    if imbalance is not None and abs(imbalance) > cfg.max_imbalance:
        start_ok = False
        reasons.append(f"order-book imbalance {imbalance:+.2f} beyond ±{cfg.max_imbalance}")
    return {"regime": regime, "grid": mode, "start_ok": start_ok, "reasons": reasons,
            "bar": str(df.index[-1]),
            **{k: (None if pd.isna(row[k]) else float(row[k]))
               for k in ("adx", "atr_pct", "atr_rank", "rsi", "support", "resistance",
                         "quote_volume_24h", "change_24h")}}


def book_imbalance(bid_qty: float, ask_qty: float, bid: float, ask: float) -> float | None:
    """(bid value - ask value) / total at the top of the book, -1..1."""
    b, a = bid_qty * bid, ask_qty * ask
    return None if b + a <= 0 else (b - a) / (b + a)
