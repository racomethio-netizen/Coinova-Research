"""Shared contract of the six strategy modules (unified brief, Phase 3).

A module turns CLOSED bars into entry signals. It never places an order: the risk engine
and execution decide later (brief sections F and G).

Signals are a DataFrame indexed by the open time (UTC) of the bar whose CLOSE produced
them, with the columns of ``SIGNAL_COLUMNS``:
* ``side``: +1 buy, -1 sell;
* ``stop``: the protective stop price, fixed at the signal;
* ``rr``: planned reward/risk. The target is set from the actual entry fill:
  ``entry + side * rr * |entry - stop|``, so the plan is exactly ``rr``:1. It is never
  below the brief's 2:1;
* ``max_bars``: time stop, in bars of the execution timeframe;
* ``exit_at``: an optional clock exit (UTC), for intraday modules; NaT otherwise.

The trade itself starts at the open of the first execution bar after the signal bar's
close (``sim.first_entry``), so a signal never trades on the bar that produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

SIGNAL_COLUMNS = ("side", "stop", "rr", "max_bars", "exit_at")
MIN_RR = 2.0                       # brief section F: minimum planned reward/risk

Frames = dict[str, pd.DataFrame]
SignalFn = Callable[[Frames, Any], pd.DataFrame]


@dataclass(frozen=True)
class ModuleSpec:
    code: str
    name: str
    market: str                    # "forex" | "crypto"
    style: str                     # "scalping" | "intraday" | "swing"
    symbols: tuple[str, ...]
    signal_tf: str
    exec_tf: str                   # bars on which stops, targets and time exits are checked
    config: Any                    # a frozen dataclass: the module's own settings
    signals: SignalFn | None       # None: a portfolio module evaluated by its own harness
    long_only: bool
    max_entry_delay: timedelta     # a signal older than this at the entry bar is dropped
    evidence: str = "new"          # "new" | "earlier_fail" | "earlier_pass"
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def config_dict(self) -> dict[str, Any]:
        if is_dataclass(self.config) and not isinstance(self.config, type):
            return asdict(self.config)
        return dict(self.config)


def empty_signals() -> pd.DataFrame:
    return pd.DataFrame({"side": pd.Series(dtype="int64"), "stop": pd.Series(dtype=float),
                         "rr": pd.Series(dtype=float), "max_bars": pd.Series(dtype="int64"),
                         "exit_at": pd.Series(dtype="datetime64[ns, UTC]")},
                        index=pd.DatetimeIndex([], tz="UTC"))


def make_signals(index: pd.Index, side: Any, stop: Any, rr: float, max_bars: int,
                 exit_at: Any = None) -> pd.DataFrame:
    if rr < MIN_RR:
        raise ValueError(f"planned reward/risk {rr} is below the minimum {MIN_RR}")
    out = pd.DataFrame({"side": pd.Series(side, index=index).astype("int64"),
                        "stop": pd.Series(stop, index=index).astype(float),
                        "rr": float(rr), "max_bars": int(max_bars)}, index=index)
    out["exit_at"] = pd.Series(exit_at, index=index) if exit_at is not None else pd.NaT
    out["exit_at"] = pd.to_datetime(out["exit_at"], utc=True)
    return out[list(SIGNAL_COLUMNS)]


def resample(df: pd.DataFrame, rule: str, *, offset: str | None = None,
             min_bars: int = 1) -> pd.DataFrame:
    """OHLC(V, spread) bars of a longer period, labelled by their open time. Bins start at
    multiples of ``rule`` after ``offset`` (UTC). Bins with fewer than ``min_bars`` source
    bars are dropped (for example the 1-hour stub that a fixed daily boundary leaves at
    the FX week's open in one season).

    Grouped explicitly: pandas ignores ``offset`` for daily bins on a timezone-aware
    index."""
    agg: dict[str, str] = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for col, how in (("volume", "sum"), ("spread", "mean")):
        if col in df:
            agg[col] = how
    if df.empty:
        return df[list(agg)].copy()
    shift = pd.Timedelta(offset) if offset else pd.Timedelta(0)
    key = (pd.DatetimeIndex(df.index) - shift).floor(rule) + shift
    g = df.groupby(key)
    out = g.agg(agg)
    out = out[g["close"].count() >= min_bars]
    out.index = pd.DatetimeIndex(out.index).as_unit("ns")
    out.index.name = None
    return out.dropna(subset=["open", "high", "low", "close"])


FX_DAY_OFFSET = "22h"              # FX day: 22:00 to 22:00 UTC (New York close, winter)
FX_DAY_MIN_BARS = 72               # at least 6 hours of M5 bars, or the bin is a stub


def _ns(df: pd.DataFrame) -> pd.DataFrame:
    """One timestamp unit everywhere (pandas may read microseconds or nanoseconds)."""
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC").as_unit("ns")
    return out


def fx_frames(m5: pd.DataFrame) -> Frames:
    """M5 bid bars with a ``spread`` column -> M5, H1 and FX-day D1 frames."""
    m5 = _ns(m5)
    return {"5m": m5, "1h": resample(m5, "1h"),
            "1d": resample(m5, "1D", offset=FX_DAY_OFFSET, min_bars=FX_DAY_MIN_BARS)}


def crypto_frames(m5: pd.DataFrame) -> Frames:
    """Binance 5m bars -> 5m, 15m, 1h, 4h and 1d (UTC) frames."""
    m5 = _ns(m5)
    return {"5m": m5, "15m": resample(m5, "15min"), "1h": resample(m5, "1h"),
            "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}
