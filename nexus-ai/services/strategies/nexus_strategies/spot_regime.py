"""Market regime engine for the spot strategy engine (docs/SPOT_ENGINE_PLAN.md).

Per CLOSED base bar (any timeframe, aligned on closed higher bars only), one label:

* LOW_LIQUIDITY   24h quote volume below ``min_quote_volume_24h``   -> block trading
* BTC_RISK_OFF    (non-BTC symbols) BTC 4h close below its 4h EMA200 -> block alt longs
* HIGH_VOLATILITY 1h ATR% above its 30-day 95th percentile or ``max_atr_pct``
                  -> reduce risk (multiplier ``high_vol_risk_mult``)
* TRENDING_UP     4h close > EMA200, 1h close > EMA200, 1h EMA50 > EMA200, ADX >= adx_min,
                  1h structure not down
* TRENDING_DOWN   4h close < EMA200 and 1h EMA50 < EMA200
* RANGING         everything else

Precedence is the order above. ``trend`` keeps the underlying trend label even when a
higher-precedence label applies (a high-volatility uptrend is still an uptrend).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher

LABELS = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "LOW_LIQUIDITY",
          "BTC_RISK_OFF")


@dataclass(frozen=True)
class RegimeConfig:
    adx_min: float = 20.0
    min_quote_volume_24h: float = 20_000_000.0      # USDT
    max_atr_pct: float = 0.03                         # 1h ATR / close
    vol_pct_window: int = 720                         # 30 days of 1h bars
    high_vol_risk_mult: float = 0.5


def _h1_features(h1: pd.DataFrame) -> pd.DataFrame:
    from nexus_analysis.structure import market_structure

    out = pd.DataFrame(index=h1.index)
    out["close"] = h1["close"]
    out["ema50"] = ind.ema(h1["close"], 50)
    out["ema200"] = ind.ema(h1["close"], 200)
    out["adx"] = ind.adx(h1, 14)
    atr = ind.atr(h1, 14)
    out["atr_pct"] = atr / h1["close"]
    out["quote_vol_24h"] = (h1["volume"] * h1["close"]).rolling(24, min_periods=24).sum()
    out["structure"] = market_structure(h1)["trend"].astype(str)
    return out


def _h4_features(h4: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"close": h4["close"], "ema200": ind.ema(h4["close"], 200)},
                        index=h4.index)


def classify_spot(base: pd.DataFrame, base_tf: str, h1: pd.DataFrame, h4: pd.DataFrame,
                  btc_h4: pd.DataFrame | None, *, is_btc: bool,
                  cfg: RegimeConfig | None = None) -> pd.DataFrame:
    """Columns: regime, trend, risk_mult, btc_risk_off, plus the inputs used (for the
    dashboard's score breakdown). Only closed higher bars are used (``align_higher``)."""
    cfg = cfg or RegimeConfig()
    f1 = _h1_features(h1)
    f1["atr_pct_p95"] = f1["atr_pct"].rolling(cfg.vol_pct_window, min_periods=200).quantile(0.95)
    df = align_higher(base[["close"]], base_tf, f1.drop(columns=["close"]), "1h", "h1_")
    df = align_higher(df, base_tf, f1[["close"]], "1h", "h1c_")
    df = align_higher(df, base_tf, _h4_features(h4), "4h", "h4_")
    if btc_h4 is not None and not is_btc:
        df = align_higher(df, base_tf, _h4_features(btc_h4), "4h", "btc4_")
        btc_off = (df["btc4_close"] < df["btc4_ema200"]).where(df["btc4_ema200"].notna())
    else:
        btc_off = pd.Series(False, index=df.index)
    up = ((df["h4_close"] > df["h4_ema200"]) & (df["h1c_close"] > df["h1_ema200"])
          & (df["h1_ema50"] > df["h1_ema200"]) & (df["h1_adx"] >= cfg.adx_min)
          & (df["h1_structure"] != "down"))
    down = (df["h4_close"] < df["h4_ema200"]) & (df["h1_ema50"] < df["h1_ema200"])
    trend = np.where(up, "TRENDING_UP", np.where(down, "TRENDING_DOWN", "RANGING"))
    high_vol = (df["h1_atr_pct"] > df["h1_atr_pct_p95"]) | (df["h1_atr_pct"] > cfg.max_atr_pct)
    low_liq = df["h1_quote_vol_24h"] < cfg.min_quote_volume_24h
    ready = df[["h4_ema200", "h1_ema200", "h1_adx", "h1_quote_vol_24h"]].notna().all(axis=1)
    regime = np.where(low_liq, "LOW_LIQUIDITY",
                      np.where(btc_off.fillna(False).astype(bool), "BTC_RISK_OFF",
                               np.where(high_vol, "HIGH_VOLATILITY", trend)))
    out = pd.DataFrame({
        "regime": pd.Series(regime, index=df.index).where(ready),
        "trend": pd.Series(trend, index=df.index).where(ready),
        "risk_mult": np.where(high_vol, cfg.high_vol_risk_mult, 1.0),
        "btc_risk_off": btc_off.fillna(False).astype(bool),
        "h4_above_ema200": df["h4_close"] > df["h4_ema200"],
        "h1_above_ema200": df["h1c_close"] > df["h1_ema200"],
        "h1_ema50_above_200": df["h1_ema50"] > df["h1_ema200"],
        "h1_adx": df["h1_adx"], "h1_structure": df["h1_structure"],
        "h1_atr_pct": df["h1_atr_pct"], "quote_vol_24h": df["h1_quote_vol_24h"]},
        index=df.index)
    return out
