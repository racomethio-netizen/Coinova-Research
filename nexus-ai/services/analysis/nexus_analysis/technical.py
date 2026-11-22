"""Technical analysis snapshot of one pair and timeframe (latest CLOSED bar), grouped as
on the dashboard. Every value comes from ``nexus_strategies.indicators`` / ``ta`` and
``nexus_analysis`` (causal). Missing history -> None, never an estimate.

Liquidity terms (equal highs/lows, sweeps, stop runs) describe price behaviour around
levels where stop orders commonly sit. They are patterns, not evidence of what any
institution is doing.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from nexus_strategies import indicators as ind
from nexus_strategies import ta

from nexus_analysis.structure import market_structure, swings
from nexus_analysis.sweeps import liquidity_sweeps

MIN_BARS = 60
LIMITATIONS = [
    "Closed candles only; the bar that is still forming is never used.",
    "EMA 200, Ichimoku spans and the volatility percentile need 200+ bars; with less "
    "history they are shown as missing.",
    "Volume is Binance spot volume for this pair only, not the whole market.",
    "Swings are confirmed 2 bars after they form, so structure events lag by 2 bars.",
    "Liquidity patterns describe price behaviour near likely stop levels; they do not show "
    "who is trading.",
]


def _v(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _b(x: Any) -> bool | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return bool(x)


def _periods_per_year(df: pd.DataFrame) -> float:
    """Bars per year from the bar spacing (crypto trades 24/7, 365 days)."""
    step = float(pd.Series(df.index).diff().dt.total_seconds().median())
    return 365 * 86400 / step


def technical_snapshot(df: pd.DataFrame, daily: pd.DataFrame | None = None) -> dict[str, Any]:
    if len(df) < MIN_BARS:
        return {"bars": len(df), "insufficient_history": True, "limitations": LIMITATIONS}
    c = df["close"]
    i = -1
    emas = ta.ema_set(c)
    d = ta.dmi(df)
    st = ta.supertrend(df)
    ich = ta.ichimoku(df)
    m = ind.macd(c)
    srsi = ta.stoch_rsi(c)
    bb = ind.bollinger(c)
    kc = ta.keltner(df)
    vr = ta.volatility_regime(df)
    rs = ta.range_state(df)
    bf = ta.breakout_follow_through(df)
    shape = ta.candle_shape(df)
    eq = ta.equal_levels(df)
    sweeps = liquidity_sweeps(df)
    ms = market_structure(df)
    sw = swings(df)
    recent = sw.tail(4)
    piv = ta.pivots_from_daily(df, daily) if daily is not None and len(daily) else None
    last_eq_high = eq["equal_high"].dropna()
    last_eq_low = eq["equal_low"].dropna()
    events = ms["event"].dropna()
    return {
        "bars": len(df), "insufficient_history": False,
        "as_of_open": df.index[i].isoformat(), "close": _v(c.iloc[i], 8),
        "trend": {
            **{k: _v(emas[k].iloc[i], 8) for k in emas.columns},
            "sma_50": _v(ind.sma(c, 50).iloc[i], 8),
            "adx": _v(d["adx"].iloc[i], 2), "plus_di": _v(d["plus_di"].iloc[i], 2),
            "minus_di": _v(d["minus_di"].iloc[i], 2),
            "supertrend": _v(st["supertrend"].iloc[i], 8),
            "supertrend_direction": _v(st["direction"].iloc[i], 0),
            "ichimoku_tenkan": _v(ich["tenkan"].iloc[i], 8),
            "ichimoku_kijun": _v(ich["kijun"].iloc[i], 8),
            "ichimoku_span_a": _v(ich["span_a"].iloc[i], 8),
            "ichimoku_span_b": _v(ich["span_b"].iloc[i], 8),
            "above_cloud": _b(ich["above_cloud"].iloc[i]),
            "below_cloud": _b(ich["below_cloud"].iloc[i]),
        },
        "momentum": {
            "rsi": _v(ind.rsi(c).iloc[i], 2), "rsi_divergence": _v(ta.rsi_divergence(df)
                                                                    .iloc[-10:].sum(), 0),
            "macd": _v(m["macd"].iloc[i], 8), "macd_signal": _v(m["signal"].iloc[i], 8),
            "macd_hist": _v(m["hist"].iloc[i], 8), "roc_12": _v(ta.roc(c).iloc[i], 3),
            "momentum_10": _v(ta.momentum(c).iloc[i], 8),
            "stoch_rsi_k": _v(srsi["k"].iloc[i], 2), "stoch_rsi_d": _v(srsi["d"].iloc[i], 2),
            "cci_20": _v(ta.cci(df).iloc[i], 2),
        },
        "volatility": {
            "atr_14": _v(ind.atr(df).iloc[i], 8),
            "historical_vol_30": _v(ta.historical_volatility(
                c, periods_per_year=_periods_per_year(df)).iloc[i], 4),
            "bb_upper": _v(bb["upper"].iloc[i], 8), "bb_lower": _v(bb["lower"].iloc[i], 8),
            "bb_width": _v(bb["width"].iloc[i], 5),
            "keltner_upper": _v(kc["upper"].iloc[i], 8),
            "keltner_lower": _v(kc["lower"].iloc[i], 8),
            "atr_percentile": _v(vr["atr_percentile"].iloc[i], 3),
            "atr_ratio": _v(vr["atr_ratio"].iloc[i], 3),
            "expanding": _b(vr["expanding"].iloc[i]),
            "contracting": _b(vr["contracting"].iloc[i]),
        },
        "volume": {
            "obv": _v(ta.obv(df).iloc[i], 2), "mfi_14": _v(ta.mfi(df).iloc[i], 2),
            "relative_volume": _v(ta.relative_volume(df["volume"]).iloc[i], 3),
            "volume_sma_20": _v(ind.sma(df["volume"], 20).iloc[i], 4),
            "volume_z": _v(ind.volume_z(df["volume"]).iloc[i], 3),
            "volume_price_divergence": _v(ta.volume_price_divergence(df).iloc[i], 0),
        },
        "structure": {
            "trend": ms["trend"].iloc[i],
            "last_swing_high": _v(ms["last_swing_high"].iloc[i], 8),
            "last_swing_low": _v(ms["last_swing_low"].iloc[i], 8),
            "last_event": events.iloc[-1] if len(events) else None,
            "last_event_at": events.index[-1].isoformat() if len(events) else None,
            "recent_swings": [{"kind": str(r["kind"]), "price": _v(r["price"], 8),
                               "at": r["swing_time"].isoformat()}
                              for _, r in recent.iterrows()],
            "range_high": _v(rs["range_high"].iloc[i], 8),
            "range_low": _v(rs["range_low"].iloc[i], 8),
            "ranging": _b(rs["ranging"].iloc[i]),
            "breakout_up": _b(rs["breakout_up"].iloc[i]),
            "breakout_down": _b(rs["breakout_down"].iloc[i]),
            "retest_recent": bool(bf[["retest_up", "retest_down"]].iloc[-5:].to_numpy().any()),
            "pivots": ({k: _v(piv[k].iloc[i], 8) for k in piv.columns}
                       if piv is not None else None),
        },
        "liquidity": {
            "equal_high": _v(last_eq_high.iloc[-1], 8) if len(last_eq_high) else None,
            "equal_low": _v(last_eq_low.iloc[-1], 8) if len(last_eq_low) else None,
            "sweep_of_low_recent": bool(sweeps["sweep_low"].iloc[-5:].any()),
            "sweep_of_high_recent": bool(sweeps["sweep_high"].iloc[-5:].any()),
            "failed_breakout_recent": bool(bf[["failed_breakout_up", "failed_breakout_down"]]
                                           .iloc[-5:].to_numpy().any()),
            "wick_rejection": _v(ta.wick_rejection(df).iloc[i], 0),
        },
        "candle": {k: _v(shape[k].iloc[i], 3) for k in shape.columns},
        "limitations": LIMITATIONS,
    }
