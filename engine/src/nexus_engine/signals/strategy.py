"""Strategy `trend_pullback` v1.0.0: rules only, no model (docs/nexus/SIGNAL_ENGINE.md §3).

Idea: trade in the direction of the higher-timeframe trend after a pullback to
the 20 EMA, once RSI recovers, momentum turns and volume confirms. Every
decision is reproducible from the candles closed at `as_of` and the frozen
parameters below. When anything is missing or uncertain the answer is HOLD.
Confidence is not calibrated (no model yet), so it is never reported as a number.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

import numpy as np

from ..timeframes import floor_to, tf_delta
from . import indicators as ta
from .levels import levels_at, nearest_above, nearest_below
from .risk import CostModel, net_reward_risk, q_price, q_ratio
from .series import HIGHER_TIMEFRAME, CandleSeries

KEY = "trend_pullback"
VERSION = "1.0.0"
NAME = "Trend pullback (rules only)"
DESCRIPTION = (
    "Trades with the higher-timeframe trend (EMA50 vs EMA200) after a pullback to the 20 EMA, when RSI(14) "
    "recovers, MACD momentum turns and volume is at least average. Entry, stop and targets come from ATR(14) and "
    "confirmed swing levels; a signal is published only if net reward/risk after fees and slippage clears the "
    "minimum. Rules only: confidence is not calibrated."
)


@dataclass(frozen=True)
class Params:
    min_bars: int = 260
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    rsi_low: float = 40.0
    rsi_high: float = 60.0
    rsi_buy_max: float = 65.0
    rsi_sell_min: float = 35.0
    atr_period: int = 14
    pullback_bars: int = 3
    min_volume_ratio: float = 1.0
    entry_below_atr: float = 0.25
    entry_above_atr: float = 0.10
    stop_atr: float = 1.5
    level_buffer_atr: float = 0.1
    min_stop_atr: float = 0.5
    max_stop_atr: float = 4.0
    target_r: tuple[float, float] = (2.0, 3.0)
    min_net_rr: float = 1.5
    min_atr_pct: float = 0.0005
    max_atr_pct: float = 0.15
    entry_window_bars: int = 6
    horizon_bars: int = 24
    costs: CostModel = field(default_factory=CostModel)

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["costs"] = self.costs.as_dict()
        d["target_r"] = list(self.target_r)
        return d


@dataclass(frozen=True)
class Proposal:
    direction: str
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    risk_reward: Decimal
    entry_expires_at: datetime
    horizon_bars: int


@dataclass(frozen=True)
class Decision:
    decision: str  # BUY | SELL | HOLD
    as_of: datetime
    reasons: tuple[str, ...]
    features: dict[str, str]
    proposal: Proposal | None = None
    explanation: dict[str, object] | None = None


def _fmt(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.8g}"


def evaluate(own: CandleSeries, htf: CandleSeries | None, as_of: datetime, p: Params = Params()) -> Decision:  # noqa: B008
    """Decide at `as_of` (a candle close boundary) using only candles closed by then."""
    s = own.closed_by(as_of)
    if len(s) < p.min_bars:
        return Decision("HOLD", as_of, ("insufficient_history",), {})
    if s.open_times[-1] + tf_delta(s.tf) != as_of:
        return Decision("HOLD", as_of, ("stale_data",), {})
    if not s.contiguous_tail(p.min_bars):
        return Decision("HOLD", as_of, ("data_quality_gap",), {})

    c, h, lo, v = s.close, s.high, s.low, s.volume
    e20, e50, e200 = ta.ema(c, p.ema_fast), ta.ema(c, p.ema_mid), ta.ema(c, p.ema_slow)
    r = ta.rsi(c, p.rsi_period)
    _, _, hist = ta.macd(c)
    a = ta.atr(h, lo, c, p.atr_period)
    vr = ta.volume_ratio(v, 20)
    t = len(s) - 1
    close, atr_t = float(c[t]), float(a[t])

    # Trend on the next higher timeframe, using only its candles closed by as_of.
    htf_tf = HIGHER_TIMEFRAME[s.tf]
    if htf_tf is None:
        t_e50, t_e200 = float(e50[t]), float(e200[t])
    else:
        if htf is None:
            return Decision("HOLD", as_of, ("htf_missing",), {})
        hs = htf.closed_by(as_of)
        if len(hs) < p.ema_slow + 10 or not hs.contiguous_tail(p.ema_slow + 10):
            return Decision("HOLD", as_of, ("htf_insufficient_history",), {})
        if hs.open_times[-1] + tf_delta(htf_tf) != floor_to(as_of, htf_tf):
            return Decision("HOLD", as_of, ("htf_stale",), {})
        t_e50, t_e200 = float(ta.ema(hs.close, p.ema_mid)[-1]), float(ta.ema(hs.close, p.ema_slow)[-1])

    features = {
        "close": _fmt(close),
        "ema20": _fmt(float(e20[t])),
        "ema50": _fmt(float(e50[t])),
        "ema200": _fmt(float(e200[t])),
        "rsi14": _fmt(float(r[t])),
        "macd_hist": _fmt(float(hist[t])),
        "atr14": _fmt(atr_t),
        "volume_ratio": _fmt(float(vr[t])),
        "htf": htf_tf or s.tf,
        "htf_ema50": _fmt(t_e50),
        "htf_ema200": _fmt(t_e200),
    }
    values = [close, atr_t, float(e20[t]), float(e200[t]), float(r[t]), float(hist[t]), float(hist[t - 1]), float(vr[t]), t_e50, t_e200]
    if not all(np.isfinite(values)):
        return Decision("HOLD", as_of, ("indicator_undefined",), features)
    atr_pct = atr_t / close
    if not p.min_atr_pct <= atr_pct <= p.max_atr_pct:
        return Decision("HOLD", as_of, ("volatility_out_of_bounds",), features)

    win = slice(t - p.pullback_bars + 1, t + 1)
    prev_rsi = r[t - p.pullback_bars : t]
    long_checks = {
        "htf_trend_up": t_e50 > t_e200,
        "above_ema200": close > float(e200[t]),
        "pullback_to_ema20": bool(np.any(lo[win] <= e20[win])),
        "rsi_recovering": bool(np.nanmin(prev_rsi) < p.rsi_low <= r[t] <= p.rsi_buy_max),
        "momentum_turning_up": float(hist[t]) > float(hist[t - 1]),
        "volume_confirms": float(vr[t]) >= p.min_volume_ratio,
    }
    short_checks = {
        "htf_trend_down": t_e50 < t_e200,
        "below_ema200": close < float(e200[t]),
        "pullback_to_ema20": bool(np.any(h[win] >= e20[win])),
        "rsi_rolling_over": bool(np.nanmax(prev_rsi) > p.rsi_high >= r[t] >= p.rsi_sell_min),
        "momentum_turning_down": float(hist[t]) < float(hist[t - 1]),
        "volume_confirms": float(vr[t]) >= p.min_volume_ratio,
    }
    if all(long_checks.values()):
        direction, checks = "BUY", long_checks
    elif all(short_checks.values()):
        direction, checks = "SELL", short_checks
    else:
        side = long_checks if long_checks["htf_trend_up"] else short_checks
        failed = tuple(f"not_{k}" for k, ok in side.items() if not ok)
        return Decision("HOLD", as_of, ("no_setup", *failed), features)

    # ---- levels
    resistances, supports = levels_at(h, lo, t, atr_t)
    buf = p.level_buffer_atr * atr_t
    if direction == "BUY":
        e_lo, e_hi = close - p.entry_below_atr * atr_t, close + p.entry_above_atr * atr_t
        mid = (e_lo + e_hi) / 2
        sup = nearest_below(supports, e_lo)
        stop = min(mid - p.stop_atr * atr_t, (sup.price - buf) if sup else float("inf"))
        risk_px = mid - stop
        targets = [mid + k * risk_px for k in p.target_r]
        res = nearest_above(resistances, e_hi)
        if res is not None and res.price - buf < targets[0]:
            targets = [res.price - buf]
        level_note = {"nearest_support": _fmt(sup.price) if sup else "none", "nearest_resistance": _fmt(res.price) if res else "none"}
    else:
        e_lo, e_hi = close - p.entry_above_atr * atr_t, close + p.entry_below_atr * atr_t
        mid = (e_lo + e_hi) / 2
        res = nearest_above(resistances, e_hi)
        stop = max(mid + p.stop_atr * atr_t, (res.price + buf) if res else float("-inf"))
        risk_px = stop - mid
        targets = [mid - k * risk_px for k in p.target_r]
        sup = nearest_below(supports, e_lo)
        if sup is not None and sup.price + buf > targets[0]:
            targets = [sup.price + buf]
        level_note = {"nearest_resistance": _fmt(res.price) if res else "none", "nearest_support": _fmt(sup.price) if sup else "none"}

    stop_atr = risk_px / atr_t
    if not p.min_stop_atr <= stop_atr <= p.max_stop_atr:
        return Decision("HOLD", as_of, ("stop_distance_out_of_bounds",), {**features, **level_note})
    if min(targets) <= 0 or stop <= 0:
        return Decision("HOLD", as_of, ("invalid_levels",), features)

    d_lo, d_hi, d_stop = q_price(e_lo), q_price(e_hi), q_price(stop)
    d_mid = (d_lo + d_hi) / 2
    tps = tuple(q_price(x) for x in targets)
    rr = net_reward_risk(direction, d_mid, d_stop, tps[0], p.costs)
    if rr < Decimal(repr(p.min_net_rr)):
        return Decision("HOLD", as_of, ("insufficient_net_reward_risk",), {**features, **level_note, "net_rr": str(q_ratio(rr))})

    step = tf_delta(s.tf)
    proposal = Proposal(
        direction=direction,
        entry_low=d_lo,
        entry_high=d_hi,
        stop_loss=d_stop,
        take_profits=tps,
        risk_reward=q_ratio(rr),
        entry_expires_at=as_of + step * p.entry_window_bars,
        horizon_bars=p.horizon_bars,
    )
    from .explain import explain  # local import keeps module graph acyclic

    reasons = tuple(checks)
    return Decision(direction, as_of, reasons, {**features, **level_note}, proposal, explain(direction, s.tf, features, level_note, proposal, p))
