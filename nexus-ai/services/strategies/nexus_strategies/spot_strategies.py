"""Spot strategy engine: Trend Pullback and Breakout + Retest (docs/SPOT_ENGINE_PLAN.md).

Both functions are causal and vectorised over CLOSED bars:
* the base timeframe is evaluated at each bar's close;
* higher timeframes are attached with ``align_higher``, using closed bars only;
* a trade decided at a bar's close is entered at the NEXT bar.

Output per base bar:
* ``candidate``: the core setup formed on this bar.
* ``entry``: a candidate that passed every check.
* ``stop``, ``tp1``, ``tp2``, ``r``: R = entry close - stop.
* ``reason``: for a rejected candidate, the first failed check, in a fixed order.
* ``score``: the share of checks passed (a rule score, not a probability).
* ``chk_*``: every individual check, for the dashboard's breakdown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher

BLOCKED_REGIMES = ("LOW_LIQUIDITY", "BTC_RISK_OFF", "TRENDING_DOWN", "RANGING")


@dataclass(frozen=True)
class TrendPullbackParams:
    pb_window: int = 8                # bars in which the pullback must have happened
    zone_atr: float = 0.5             # EMA zone extends this far below EMA50
    support_tol_atr: float = 0.3      # touch of the last confirmed 1h swing low
    vol_mult: float = 1.2             # volume vs the previous 20 bars' average
    ext_atr: float = 1.0              # max distance of close above EMA20, in ATR
    stop_buffer_atr: float = 0.2      # below the pullback low
    min_stop_atr: float = 1.0         # ATR-aware: stop at least this far
    max_stop_atr: float = 3.0         # reject wider stops
    atr_pct_min: float = 0.0015       # too quiet: costs dominate
    atr_pct_max: float = 0.02
    rr_min: float = 2.0               # room to the nearest 1h resistance, in R
    resistance_bars: int = 50         # 1h bars searched for resistance
    tp1_r: float = 1.5
    tp2_r: float = 3.0


@dataclass(frozen=True)
class BreakoutParams:
    range_n: int = 24                 # consolidation lookback (base bars)
    max_width_atr: float = 4.0        # range narrower than this many ATRs
    vol_mult: float = 1.5             # breakout volume vs the previous 20 bars' average
    min_body: float = 0.5             # body / range of the breakout bar (no wick-only)
    max_ext_atr: float = 1.5          # close no further than this above the level
    retest_window: int = 6
    retest_tol_atr: float = 0.3
    require_retest: bool = True
    stop_buffer_atr: float = 0.2
    min_stop_atr: float = 0.5
    max_stop_atr: float = 3.0
    atr_pct_min: float = 0.0015
    atr_pct_max: float = 0.03
    rr_min: float = 2.0
    resistance_bars: int = 200
    tp1_r: float = 1.5
    tp2_r: float = 3.0


def _finish(df: pd.DataFrame, candidate: pd.Series, checks: dict[str, pd.Series],
            stop: pd.Series, p: TrendPullbackParams | BreakoutParams) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    chk = pd.DataFrame({k: v.fillna(False).astype(bool) for k, v in checks.items()})
    r = df["close"] - stop
    out["candidate"] = candidate.fillna(False).astype(bool)
    out["entry"] = out["candidate"] & chk.all(axis=1)
    out["stop"], out["r"] = stop, r
    out["tp1"] = df["close"] + p.tp1_r * r
    out["tp2"] = df["close"] + p.tp2_r * r
    out["score"] = chk.mean(axis=1)
    reason = pd.Series(None, index=df.index, dtype=object)
    for name in reversed(list(checks)):      # first failing check in the stated order wins
        reason = reason.mask(~chk[name], name)
    out["reason"] = reason.where(out["candidate"] & ~out["entry"])
    for k in chk:
        out[f"chk_{k}"] = chk[k]
    return out


def _resistance(h1: pd.DataFrame, bars: int) -> pd.DataFrame:
    return pd.DataFrame({"res": h1["high"].rolling(bars, min_periods=bars).max()},
                        index=h1.index)


def trend_pullback(m15: pd.DataFrame, h1: pd.DataFrame, regime: pd.DataFrame,
                   p: TrendPullbackParams | None = None) -> pd.DataFrame:
    """``regime``: ``spot_regime.classify_spot`` on the 15m index."""
    from nexus_analysis.structure import market_structure

    p = p or TrendPullbackParams()
    df = m15.copy()
    df["ema20"], df["ema50"] = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
    df["atr"], df["rsi"] = ind.atr(df, 14), ind.rsi(df["close"], 14)
    rv = df["volume"] / df["volume"].shift(1).rolling(20, min_periods=20).mean()
    h1s = market_structure(h1)[["last_swing_low"]].join(_resistance(h1, p.resistance_bars))
    df = align_higher(df, "15m", h1s, "1h", "h1_")
    # Pullback into the EMA20-EMA50 zone, or a touch of the last confirmed 1h swing low.
    ema_touch = (df["low"] <= df["ema20"]) & (df["low"] >= df["ema50"] - p.zone_atr * df["atr"])
    sup_touch = (df["low"] <= df["h1_last_swing_low"] + p.support_tol_atr * df["atr"]) \
        & (df["close"] > df["h1_last_swing_low"])
    pulled = (ema_touch | sup_touch).astype(float).rolling(p.pb_window, min_periods=1).max() == 1
    rsi_dipped = df["rsi"].rolling(p.pb_window, min_periods=1).min() < 50
    rsi_recovers = (df["rsi"] > 50) & (df["rsi"].shift(1) <= 50)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    bullish = (df["close"] > df["open"]) & ((df["close"] - df["low"]) / rng >= 0.6) \
        & (df["close"] > df["ema20"])
    candidate = pulled & rsi_dipped & rsi_recovers & bullish
    low_pb = df["low"].rolling(p.pb_window, min_periods=1).min()
    stop = np.minimum(low_pb - p.stop_buffer_atr * df["atr"],
                      df["close"] - p.min_stop_atr * df["atr"])
    r = df["close"] - stop
    atr_pct = df["atr"] / df["close"]
    room = df["h1_res"] - df["close"]
    checks = {
        "htf_4h_trend": regime["h4_above_ema200"],
        "above_1h_ema200": regime["h1_above_ema200"],
        "ema50_above_ema200": regime["h1_ema50_above_200"],
        "adx_strength": regime["h1_adx"] >= 20.0,
        "structure_not_down": regime["h1_structure"] != "down",
        "btc_not_risk_off": ~regime["btc_risk_off"].astype(bool),
        "regime_allows": ~regime["regime"].isin(BLOCKED_REGIMES) & regime["regime"].notna()
        & (regime["trend"] == "TRENDING_UP"),
        "atr_in_range": (atr_pct >= p.atr_pct_min) & (atr_pct <= p.atr_pct_max),
        "volume_confirmed": rv >= p.vol_mult,
        "not_extended": (df["close"] - df["ema20"]) <= p.ext_atr * df["atr"],
        "stop_sane": (r > 0) & (r <= p.max_stop_atr * df["atr"]),
        "reward_risk": (room <= 0) | (room >= p.rr_min * r),
    }
    return _finish(df, candidate, checks, stop, p)


def breakout_retest(base: pd.DataFrame, regime: pd.DataFrame,
                    p: BreakoutParams | None = None) -> pd.DataFrame:
    """Consolidation breakout with a confirmed close and volume, entered on the retest."""
    p = p or BreakoutParams()
    df = base.copy()
    df["atr"] = ind.atr(df, 14)
    res = df["high"].shift(1).rolling(p.range_n, min_periods=p.range_n).max()
    sup = df["low"].shift(1).rolling(p.range_n, min_periods=p.range_n).min()
    consolidating = ((res - sup) / df["atr"]) <= p.max_width_atr
    vol_avg = df["volume"].shift(1).rolling(20, min_periods=20).mean()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]) / rng
    breakout = (df["close"] > res) & consolidating.shift(1).fillna(False).astype(bool) \
        & (df["volume"] >= p.vol_mult * vol_avg) & (body >= p.min_body)
    far_res = df["high"].shift(p.range_n + 1).rolling(p.resistance_bars,
                                                       min_periods=p.range_n).max()
    lo, c, o = (df[k].to_numpy(float) for k in ("low", "close", "open"))
    atr = df["atr"].to_numpy(float)
    level = np.full(len(df), np.nan)
    retest_low = np.full(len(df), np.nan)
    cand = np.zeros(len(df), dtype=bool)
    active: list[tuple[int, float, float]] = []       # (bar, level, lowest low since)
    bo = breakout.fillna(False).to_numpy()
    resv = res.to_numpy(float)
    for i in range(len(df)):
        keep = []
        for start, lv, mn in active:
            if i - start > p.retest_window or c[i] < lv:   # expired, or failed breakout
                continue
            mn = min(mn, lo[i])
            if not np.isnan(atr[i]) and lo[i] <= lv + p.retest_tol_atr * atr[i] \
                    and c[i] > lv and c[i] > o[i]:
                cand[i], level[i], retest_low[i] = True, lv, mn
                continue
            keep.append((start, lv, mn))
        active = keep
        if bo[i]:
            if p.require_retest:
                active.append((i, resv[i], lo[i]))
            else:
                cand[i], level[i], retest_low[i] = True, resv[i], lo[i]
    lv_s = pd.Series(level, index=df.index)
    stop = np.minimum(np.minimum(pd.Series(retest_low, index=df.index), lv_s)
                      - p.stop_buffer_atr * df["atr"], df["close"] - p.min_stop_atr * df["atr"])
    r = df["close"] - stop
    atr_pct = df["atr"] / df["close"]
    room = far_res - df["close"]
    checks = {
        "btc_not_risk_off": ~regime["btc_risk_off"].astype(bool),
        "regime_allows": ~regime["regime"].isin(("LOW_LIQUIDITY", "BTC_RISK_OFF",
                                                  "TRENDING_DOWN")) & regime["regime"].notna(),
        "atr_in_range": (atr_pct >= p.atr_pct_min) & (atr_pct <= p.atr_pct_max),
        "not_extended": (df["close"] - lv_s) <= p.max_ext_atr * df["atr"],
        "stop_sane": (r > 0) & (r <= p.max_stop_atr * df["atr"]),
        "reward_risk": room.isna() | (room <= 0) | (room >= p.rr_min * r),
    }
    out = _finish(df, pd.Series(cand, index=df.index), checks, stop, p)
    out["level"] = lv_s
    return out


def params_dict(p: TrendPullbackParams | BreakoutParams) -> dict[str, float | int | bool]:
    return asdict(p)
