"""Candidate setups, their features, and labels from the shared execution model.

A candidate is a rule-passing setup *before* the reward/risk gate, so a model can
learn which setups to take. Labels: 1 if TP1 is hit before the stop, 0 if the stop
is hit first; horizon exits are labeled by the sign of the net return; setups that
never fill are excluded. Each sample keeps its label window [t, t_end] for purging.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from ..backtest.engine import iter_decisions
from ..marketdata.types import Candle
from ..signals.execution import Bar, Plan, resolve
from ..signals.strategy import Decision, Params

FEATURES = (
    "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr", "rsi14", "macd_hist_atr", "volume_ratio",
    "atr_pct", "htf_trend", "direction", "net_rr",
)  # fmt: skip


@dataclass(frozen=True)
class Dataset:
    X: NDArray[np.float64]
    y: NDArray[np.int64]
    t: tuple[datetime, ...]
    t_end: tuple[datetime, ...]
    net_return: NDArray[np.float64]
    net_rr: NDArray[np.float64]

    def __len__(self) -> int:
        return len(self.y)

    def take(self, idx: Sequence[int] | NDArray[np.int64]) -> Dataset:
        ix = np.asarray(idx, dtype=np.int64)
        return Dataset(self.X[ix], self.y[ix], tuple(self.t[i] for i in ix), tuple(self.t_end[i] for i in ix), self.net_return[ix], self.net_rr[ix])


def feature_vector(d: Decision) -> list[float]:
    assert d.proposal is not None
    f = {k: float(v) for k, v in d.features.items() if k not in ("htf", "nearest_support", "nearest_resistance")}
    atr, close = f["atr14"], f["close"]
    return [
        (close - f["ema20"]) / atr, (close - f["ema50"]) / atr, (close - f["ema200"]) / atr, f["rsi14"] / 100,
        f["macd_hist"] / atr, f["volume_ratio"], atr / close, (f["htf_ema50"] - f["htf_ema200"]) / f["htf_ema200"],
        1.0 if d.decision == "BUY" else -1.0, float(d.proposal.risk_reward),
    ]  # fmt: skip


def build_dataset(tf: str, candles: Sequence[Candle], htf: Sequence[Candle] | None, start: datetime, end: datetime, params: Params) -> Dataset:
    candidate_params = replace(params, min_net_rr=0.0)  # every rule-passing setup; the model decides
    finals = sorted((c for c in candles if c.is_final), key=lambda c: c.open_time)
    bars = [Bar(c.open_time, c.open, c.high, c.low, c.close) for c in finals]
    X: list[list[float]] = []
    y: list[int] = []
    t: list[datetime] = []
    t_end: list[datetime] = []
    rets: list[float] = []
    for i, as_of, d in iter_decisions(tf, candles, htf, start, end, candidate_params):
        if d.decision == "HOLD" or d.proposal is None:
            continue
        p = d.proposal
        plan = Plan(d.decision, p.entry_low, p.entry_high, p.stop_loss, p.take_profits[0], params.entry_window_bars, p.horizon_bars, params.costs)
        res = resolve(plan, bars[i + 1 : i + 2 + params.entry_window_bars + p.horizon_bars], tf, as_of)
        if res.outcome is None or res.closed_at is None or res.entry_price is None or res.net_return is None:
            continue  # unresolved at the data's end, never filled, or invalidated
        label = 1 if res.outcome == "win" else 0 if res.outcome == "loss" else int(res.net_return > 0)
        X.append(feature_vector(d))
        y.append(label)
        t.append(as_of)
        t_end.append(res.closed_at)
        rets.append(float(res.net_return))
    Xa = np.array(X, dtype=np.float64).reshape(-1, len(FEATURES))
    return Dataset(Xa, np.array(y, dtype=np.int64), tuple(t), tuple(t_end), np.array(rets, dtype=np.float64), Xa[:, FEATURES.index("net_rr")].copy())


def dataset_hash(candles: Sequence[Candle]) -> str:
    """Content hash of the candles an experiment used (reproducibility record)."""
    h = hashlib.sha256()
    for c in sorted(candles, key=lambda c: c.open_time):
        h.update(f"{c.open_time.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}\n".encode())
    return h.hexdigest()
