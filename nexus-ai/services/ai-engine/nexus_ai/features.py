"""Causal feature matrix on the 15m base timeframe with 1h/4h context.

Every feature at row t is computable at the close of bar t. Higher-timeframe values are
attached with ``align_higher`` (closed higher bars only). ``FEATURE_VERSION`` changes
whenever the definition changes so stored models can't be fed mismatched features.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher
from nexus_strategies.regime import classify

FEATURE_VERSION = "f1"
BASE_TF = "15m"


def _block(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    c = df["close"]
    a = ind.atr(df, 14)
    bb = ind.bollinger(c, 20)
    m = ind.macd(c)
    out = pd.DataFrame(index=df.index)
    for n in (1, 4, 16):
        out[f"{prefix}ret_{n}"] = np.log(c / c.shift(n))
    out[f"{prefix}rsi"] = ind.rsi(c, 14)
    out[f"{prefix}atr_pct"] = a / c
    out[f"{prefix}adx"] = ind.adx(df, 14)
    out[f"{prefix}ema20_dist"] = (c - ind.ema(c, 20)) / a
    out[f"{prefix}ema50_dist"] = (c - ind.ema(c, 50)) / a
    out[f"{prefix}bb_pos"] = (c - bb["lower"]) / (bb["upper"] - bb["lower"])
    out[f"{prefix}bb_width"] = bb["width"]
    out[f"{prefix}macd_hist"] = m["hist"] / a
    out[f"{prefix}vol_z"] = ind.volume_z(df["volume"], 50)
    return out


def build_features(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base = frames[BASE_TF]
    feats = _block(base, "")
    c = base["close"]
    feats["ret_96"] = np.log(c / c.shift(96))
    feats["rvol_96"] = ind.realized_vol(c, 96)
    feats["rvol_rank"] = ind.rolling_percentile(feats["rvol_96"], 2000)
    feats["range_pos_96"] = (c - base["low"].rolling(96).min()) / (
        base["high"].rolling(96).max() - base["low"].rolling(96).min())
    regime = classify(base)
    for r in ("trend_up", "trend_down", "range", "high_vol"):
        feats[f"regime_{r}"] = (regime == r).astype(float).where(regime.notna())
    # Time of day/week at the bar CLOSE (when the decision is made).
    close_time = base.index + pd.Timedelta(minutes=15)
    hours = close_time.hour + close_time.minute / 60
    feats["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    feats["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    feats["dow"] = close_time.day_of_week.astype(float)
    for tf, prefix in (("1h", "h1_"), ("4h", "h4_")):
        block = _block(frames[tf], "")
        feats = align_higher(feats, BASE_TF, block, tf, prefix)
    return feats.replace([np.inf, -np.inf], np.nan)


def feature_hash(columns: list[str]) -> str:
    return hashlib.sha256((FEATURE_VERSION + "|" + ",".join(columns)).encode()).hexdigest()
