"""Crypto modules 4 to 6 (unified brief section C). Binance spot, long only (NEXUS's spot
book never sells short; futures stay disabled). Frames: ``crypto_frames`` (5m, 15m, 1h,
4h, 1d). Every value at a bar comes from that bar or earlier.

* Module 4 is a new rule, fixed in the pre-registration.
* Module 5 wraps the EXISTING breakout-and-retest rule (``spot_strategies.breakout_retest``
  on 1h, as ``breakout_retest_v2`` in the spot engine), unchanged. It failed its
  pre-registered test on 23 Nov 2026 (profit factor 0.71); that verdict stands.
* Module 6 is the EXISTING "Catching Crypto Trends" rule (``nexus_backtest.crypto_trends``),
  which passed on 23 Nov 2026 and runs as a forward paper test (``trend-paper``). It is a
  portfolio-weight rule, not a bracket trade, so it keeps its own harness and worker.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher
from nexus_strategies.modules.base import Frames, empty_signals, make_signals

# ----------------------------------------------------------------------------- module 4


@dataclass(frozen=True)
class CryptoScalpConfig:
    """Scalping volume momentum on 5m, with the 1h trend."""

    h1_fast: int = 20
    h1_slow: int = 50
    vol_window: int = 48            # median volume of the previous 4 hours
    vol_mult: float = 3.0
    breakout: int = 12              # close above the previous hour's high
    close_location: float = 0.75    # close in the top quarter of the bar
    atr_n: int = 14
    stop_buffer_atr: float = 0.25
    min_stop_atr: float = 1.0
    max_stop_atr: float = 4.0
    round_trip_cost: float = 0.003  # 2 x (0.10% fee + 0.05% slippage)
    max_cost_of_risk: float = 0.25  # skip unless costs are <= 25% of the risk
    rr: float = 2.0
    max_bars: int = 48              # 4 hours of 5m


def crypto_scalp_signals(frames: Frames, cfg: CryptoScalpConfig) -> pd.DataFrame:
    m5, h1 = frames["5m"], frames["1h"]
    if len(m5) < cfg.vol_window + 1 or len(h1) < cfg.h1_slow:
        return empty_signals()
    f = m5.copy()
    f["atr"] = ind.atr(f, cfg.atr_n)
    h = pd.DataFrame({"c": h1["close"], "ef": ind.ema(h1["close"], cfg.h1_fast),
                      "es": ind.ema(h1["close"], cfg.h1_slow)}, index=h1.index)
    f = align_higher(f, "5m", h, "1h", "h1_")
    trend = (f["h1_c"] > f["h1_es"]) & (f["h1_ef"] > f["h1_es"])
    med = f["volume"].shift(1).rolling(cfg.vol_window, min_periods=cfg.vol_window).median()
    spike = f["volume"] >= cfg.vol_mult * med
    brk = f["close"] > f["high"].shift(1).rolling(cfg.breakout, min_periods=cfg.breakout).max()
    rng = (f["high"] - f["low"]).replace(0, np.nan)
    strong = ((f["close"] - f["low"]) / rng >= cfg.close_location) & (f["close"] > f["open"])
    atr = f["atr"]
    dist = np.maximum(f["close"] - (f["low"] - cfg.stop_buffer_atr * atr),
                      cfg.min_stop_atr * atr)
    cheap = cfg.round_trip_cost * f["close"] <= cfg.max_cost_of_risk * dist
    sel = (trend & spike & brk & strong & (dist <= cfg.max_stop_atr * atr) & cheap)
    sel = sel.fillna(False).astype(bool).to_numpy()
    return make_signals(f.index[sel], np.ones(int(sel.sum()), dtype=int),
                        (f["close"] - dist).to_numpy()[sel], cfg.rr, cfg.max_bars)


# ----------------------------------------------------------------------------- module 5


@dataclass(frozen=True)
class CryptoBreakoutRetestConfig:
    """The existing 1h breakout-and-retest rule (``BreakoutParams`` defaults) with the
    spot regime filter, a single 2R target and a 48-hour time stop."""

    rr: float = 2.0
    max_hours: int = 48
    exec_bars_per_hour: int = 12    # 5m execution bars


def crypto_breakout_retest_signals(frames: Frames, cfg: CryptoBreakoutRetestConfig
                                   ) -> pd.DataFrame:
    from nexus_strategies.spot_regime import classify_spot
    from nexus_strategies.spot_strategies import BreakoutParams, breakout_retest

    h1, h4 = frames["1h"], frames["4h"]
    if len(h1) < 250 or len(h4) < 200:
        return empty_signals()
    btc_h4 = frames.get("btc_4h")         # given for altcoins only (BTC risk-off filter)
    reg = classify_spot(h1, "1h", h1, h4, btc_h4, is_btc=btc_h4 is None)
    bo = breakout_retest(h1, reg, BreakoutParams())
    sel = bo["entry"].fillna(False).astype(bool).to_numpy()
    return make_signals(h1.index[sel], np.ones(int(sel.sum()), dtype=int),
                        bo["stop"].to_numpy()[sel], cfg.rr, cfg.max_hours * cfg.exec_bars_per_hour)


# ----------------------------------------------------------------------------- module 6


@dataclass(frozen=True)
class CryptoSwingConfig:
    """"Catching Crypto Trends" as pre-registered on 23 Nov 2026: Donchian lookbacks
    5-360 days, 25% volatility target per lookback, 100% cap, BTC and ETH half each."""

    lookbacks: tuple[int, ...] = (5, 10, 20, 30, 60, 90, 150, 250, 360)
    vol_target: float = 0.25
    cap: float = 1.0
    rebalance_band: float = 0.20
    fee: float = 0.0010
    slippage: float = 0.0005
