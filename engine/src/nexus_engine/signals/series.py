"""Candle series for analysis, with strict as-of slicing (no look-ahead)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

import numpy as np

from ..marketdata.types import Candle
from ..timeframes import tf_delta
from .indicators import Arr

HIGHER_TIMEFRAME: dict[str, str | None] = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": None}


@dataclass(frozen=True)
class CandleSeries:
    tf: str
    open_times: tuple[datetime, ...]
    open: Arr
    high: Arr
    low: Arr
    close: Arr
    volume: Arr

    @staticmethod
    def from_candles(tf: str, candles: Sequence[Candle]) -> CandleSeries:
        cs = sorted((c for c in candles if c.is_final), key=lambda c: c.open_time)
        return CandleSeries(
            tf=tf,
            open_times=tuple(c.open_time for c in cs),
            open=np.array([float(c.open) for c in cs], dtype=np.float64),
            high=np.array([float(c.high) for c in cs], dtype=np.float64),
            low=np.array([float(c.low) for c in cs], dtype=np.float64),
            close=np.array([float(c.close) for c in cs], dtype=np.float64),
            volume=np.array([float(c.volume) for c in cs], dtype=np.float64),
        )

    def __len__(self) -> int:
        return len(self.open_times)

    def closed_by(self, as_of: datetime) -> CandleSeries:
        """Only candles that had fully closed at `as_of` (open_time + tf <= as_of)."""
        step = tf_delta(self.tf)
        k = 0
        for t in self.open_times:
            if t + step <= as_of:
                k += 1
            else:
                break
        return CandleSeries(self.tf, self.open_times[:k], self.open[:k], self.high[:k], self.low[:k], self.close[:k], self.volume[:k])

    def window(self, end_index: int, bars: int) -> CandleSeries:
        """The `bars` candles ending at `end_index` (inclusive), as array views."""
        s = slice(max(0, end_index - bars + 1), end_index + 1)
        return CandleSeries(self.tf, self.open_times[s], self.open[s], self.high[s], self.low[s], self.close[s], self.volume[s])

    def contiguous_tail(self, bars: int) -> bool:
        if len(self) < bars:
            return False
        step = tf_delta(self.tf)
        tail = self.open_times[-bars:]
        return all(b - a == step for a, b in pairwise(tail))
