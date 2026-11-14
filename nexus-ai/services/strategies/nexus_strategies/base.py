"""Strategy interface. Strategies are long-only (spot) and vectorized.

``compute`` returns, for every base-timeframe bar (evaluated at that bar's CLOSE):

* ``entry``  (bool)  open a long at the next bar's open
* ``exit``   (bool)  close an open long at the next bar's open (strategy exit)
* ``stop``, ``target`` (float) protective levels derived from this bar's close
* ``confidence`` (0..1) rule-strength score (not a probability)
* ``regime``  market regime label at this bar

Entries are suppressed in the ``high_vol`` regime and when inputs are missing: the
strategy never "forces" a trade.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import pandas as pd

from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher
from nexus_strategies.regime import classify

OUTPUT_COLUMNS = ["entry", "exit", "stop", "target", "confidence", "regime"]


@dataclass(frozen=True)
class Signal:
    strategy: str
    symbol: str
    timeframe: str
    bar_time: datetime
    entry_price: float
    stop: float
    target: float
    confidence: float
    regime: str | None
    details: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    code: ClassVar[str]
    base_tf: ClassVar[str]
    confirm_tf: ClassVar[str]
    defaults: ClassVar[dict[str, float]]

    # Shared by all strategies: the target must be at least ``min_edge_x`` times the
    # assumed round-trip cost (fees + slippage, both legs) away from entry.
    # ``use_exit`` 0/1 toggles the discretionary strategy exit (stops/targets always apply).
    common_defaults: ClassVar[dict[str, float]] = {"min_edge_x": 3.0,
                                                   "round_trip_cost": 0.003,
                                                   "use_exit": 1.0}

    def __init__(self, **params: float) -> None:
        allowed = set(self.defaults) | set(self.common_defaults)
        unknown = set(params) - allowed
        if unknown:
            raise ValueError(f"unknown parameters for {self.code}: {sorted(unknown)}")
        self.params: dict[str, float] = {**self.common_defaults, **self.defaults, **params}

    @property
    def timeframes(self) -> tuple[str, str]:
        return self.base_tf, self.confirm_tf

    def prepare(self, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        base = frames[self.base_tf].copy()
        base["atr"] = ind.atr(base, 14)
        base["regime"] = classify(base)
        higher = frames[self.confirm_tf].copy()
        higher["ema20"] = ind.ema(higher["close"], 20)
        higher["ema50"] = ind.ema(higher["close"], 50)
        higher["rsi"] = ind.rsi(higher["close"], 14)
        higher["adx"] = ind.adx(higher, 14)
        return align_higher(base, self.base_tf, higher[["close", "ema20", "ema50", "rsi", "adx"]],
                            self.confirm_tf, "h_")

    @abstractmethod
    def rules(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return entry/exit/stop/target/confidence columns for the prepared frame."""

    def compute(self, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        df = self.prepare(frames)
        out = self.rules(df)
        out["regime"] = df["regime"]
        valid = out[["stop", "target"]].notna().all(axis=1) & df["atr"].gt(0)
        valid &= out["stop"] < df["close"]
        valid &= out["target"] > df["close"]
        edge = (out["target"] - df["close"]) / df["close"]
        valid &= edge >= self.params["min_edge_x"] * self.params["round_trip_cost"]
        out["entry"] = out["entry"].fillna(False).astype(bool) & valid & (
            df["regime"].notna() & (df["regime"] != "high_vol"))
        out["exit"] = out["exit"].fillna(False).astype(bool) & bool(self.params["use_exit"])
        out["confidence"] = out["confidence"].clip(0, 1).fillna(0.0)
        return out[OUTPUT_COLUMNS]

    def latest(self, frames: dict[str, pd.DataFrame], symbol: str) -> Signal | None:
        """Signal for the most recent CLOSED base bar, if any."""
        out = self.compute(frames)
        if out.empty or not bool(out["entry"].iloc[-1]):
            return None
        row = out.iloc[-1]
        bar_time = out.index[-1].to_pydatetime()
        close = float(frames[self.base_tf]["close"].iloc[-1])
        return Signal(self.code, symbol, self.base_tf, bar_time, close, float(row["stop"]),
                      float(row["target"]), float(row["confidence"]), row["regime"],
                      {"params": self.params})
