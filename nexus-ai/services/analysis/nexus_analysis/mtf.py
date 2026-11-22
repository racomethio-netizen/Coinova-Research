"""Multi-timeframe analysis (Phase C2), causal by construction.

For each timeframe, ``tf_state`` derives per-bar states from CLOSED bars:

* trend     mean of three votes in [-1, 1]: EMA20 vs EMA50, close vs EMA200, Supertrend
            direction (votes that lack history are skipped, never guessed)
* momentum  mean of RSI (>55 +1, <45 -1, else 0), MACD histogram sign, ROC sign
* volatility +1 expanding, -1 contracting, 0 normal (short vs long ATR)
* structure +1 / -1 / 0 from confirmed swings (HH/HL, LH/LL, BOS/CHoCH)

``conflict`` = the base timeframe and the higher timeframes lean opposite ways, each by a
majority of their votes (|score| > 0.3).

``mtf_frame`` attaches every other timeframe's states to the rows of a base timeframe with
``align_higher``: a base bar evaluated at its close sees only bars of the other timeframe
that had CLOSED by then (this works for lower and higher timeframes alike). It then adds
agreement scores, higher-timeframe confirmation and a conflict flag.

These are FEATURES. Whether and how timeframes must agree for a trade is a rule that has to
be learned and validated out of sample (Phases D/E); nothing here blocks or triggers a
trade on its own.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies import indicators as ind
from nexus_strategies import ta
from nexus_strategies.frames import TF_SECONDS, align_higher

from nexus_analysis.structure import market_structure

MTF_TIMEFRAMES = ("1m", "3m", "5m", "15m", "1h", "4h", "1d")
STATE_COLUMNS = ("trend", "momentum", "volatility", "structure")
FEATURE_COLUMNS = ("ema20_dist", "ema50_dist", "ema200_dist", "rsi", "macd_hist_pct", "adx",
                   "di_spread", "atr_pct", "bb_width", "rel_volume", "roc")


def _votes(*series: pd.Series) -> pd.Series:
    """Mean of the available votes (NaN where no vote is available)."""
    return pd.concat(series, axis=1).mean(axis=1, skipna=True)


def tf_state(df: pd.DataFrame) -> pd.DataFrame:
    """States and normalized features of one timeframe (row t uses rows <= t only)."""
    c = df["close"]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    st_dir = ta.supertrend(df)["direction"]
    trend = _votes(np.sign(e20 - e50).where(e50.notna()),
                   np.sign(c - e200).where(e200.notna()), st_dir)
    r = ind.rsi(c)
    hist = ind.macd(c)["hist"]
    rc = ta.roc(c, 12)
    momentum = _votes(pd.Series(np.where(r > 55, 1.0, np.where(r < 45, -1.0, 0.0)),
                                index=df.index).where(r.notna()),
                      np.sign(hist).where(hist.notna()), np.sign(rc).where(rc.notna()))
    vr = ta.volatility_regime(df, short=14, long=50, pct_window=100)
    volatility = pd.Series(np.where(vr["expanding"] == True, 1.0,  # noqa: E712 (object col)
                                    np.where(vr["contracting"] == True, -1.0, 0.0)),  # noqa: E712
                           index=df.index).where(vr["atr_ratio"].notna())
    ms = market_structure(df)
    structure = ms["trend"].map({"up": 1.0, "down": -1.0, "undefined": 0.0}).astype(float)
    d = ta.dmi(df)
    a = ind.atr(df, 14)
    return pd.DataFrame({
        "trend": trend, "momentum": momentum, "volatility": volatility,
        "structure": structure,
        "ema20_dist": c / e20 - 1, "ema50_dist": c / e50 - 1, "ema200_dist": c / e200 - 1,
        "rsi": r, "macd_hist_pct": hist / c, "adx": d["adx"],
        "di_spread": d["plus_di"] - d["minus_di"], "atr_pct": a / c,
        "bb_width": ind.bollinger(c)["width"], "rel_volume": ta.relative_volume(df["volume"]),
        "roc": rc,
    }, index=df.index)


def mtf_frame(frames: dict[str, pd.DataFrame], base_tf: str) -> pd.DataFrame:
    """Per base bar: every timeframe's states and features (``<tf>_<col>``) plus scores."""
    if base_tf not in frames:
        raise ValueError(f"base timeframe {base_tf} missing")
    tfs = [tf for tf in MTF_TIMEFRAMES if tf in frames and not frames[tf].empty]
    base = frames[base_tf]
    out = base[[]].copy()
    for tf in tfs:
        state = tf_state(frames[tf])
        if tf == base_tf:
            out = out.join(state.add_prefix(f"{tf}_"))
        else:
            out = align_higher(out, base_tf, state, tf, f"{tf}_")
    higher = [tf for tf in tfs if TF_SECONDS[tf] > TF_SECONDS[base_tf]]

    def mean_of(col: str, among: list[str]) -> pd.Series:
        cols = [f"{tf}_{col}" for tf in among]
        return out[cols].mean(axis=1, skipna=True) if cols else pd.Series(np.nan, out.index)

    out["trend_agreement"] = mean_of("trend", tfs)
    out["momentum_agreement"] = mean_of("momentum", tfs)
    out["volatility_agreement"] = mean_of("volatility", tfs)
    out["structure_score"] = mean_of("structure", tfs)
    out["htf_trend"] = mean_of("trend", higher)
    if higher:
        h = out[[f"{tf}_trend" for tf in higher]]
        n = h.notna().sum(axis=1)
        out["htf_confirm_long"] = (h.gt(0).sum(axis=1) / n).where(n > 0)
        out["htf_confirm_short"] = (h.lt(0).sum(axis=1) / n).where(n > 0)
    else:
        out["htf_confirm_long"] = np.nan
        out["htf_confirm_short"] = np.nan
    base_trend = out[f"{base_tf}_trend"]
    out["conflict"] = ((np.sign(base_trend) * np.sign(out["htf_trend"]) < 0)
                       & (base_trend.abs() > 0.3) & (out["htf_trend"].abs() > 0.3)
                       ).astype(float).where(base_trend.notna() & out["htf_trend"].notna())
    return out


def mtf_snapshot(frames: dict[str, pd.DataFrame], base_tf: str) -> dict[str, Any]:
    """The latest base bar as a readable summary with per-timeframe states."""
    f = mtf_frame(frames, base_tf)
    if f.empty:
        return {"base_timeframe": base_tf, "as_of": None, "timeframes": {}, "scores": {}}
    last = f.iloc[-1]

    def val(x: Any) -> float | None:
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)

    per_tf = {}
    for tf in MTF_TIMEFRAMES:
        if f"{tf}_trend" not in f.columns:
            continue
        per_tf[tf] = {c: val(last.get(f"{tf}_{c}")) for c in (*STATE_COLUMNS, *FEATURE_COLUMNS)}
        bars = frames.get(tf)
        per_tf[tf]["bars"] = len(bars) if bars is not None else 0
        per_tf[tf]["last_bar"] = bars.index[-1].isoformat() if bars is not None and len(bars) else None
    scores = {k: val(last.get(k)) for k in ("trend_agreement", "momentum_agreement",
                                            "volatility_agreement", "structure_score",
                                            "htf_trend", "htf_confirm_long",
                                            "htf_confirm_short", "conflict")}
    close_time = f.index[-1] + pd.Timedelta(seconds=TF_SECONDS[base_tf])
    return {"base_timeframe": base_tf, "as_of": close_time.isoformat(),
            "timeframes": per_tf, "scores": scores}
