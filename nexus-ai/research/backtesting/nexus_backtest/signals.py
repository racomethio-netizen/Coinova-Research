"""Signal-level outcome study (unconstrained by portfolio limits).

For every entry signal: enter at the next execution bar's open (+ slippage), then exit
at the first of stop (priority on ties), target, strategy exit, or ``max_hold``; net of
fees and slippage on both legs. This measures a strategy's raw edge across the full
history and supplies triple-barrier labels for the AI engine.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from nexus_strategies.base import Strategy
from nexus_strategies.frames import TF_SECONDS


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005

    @property
    def round_trip(self) -> float:
        return 2 * (self.fee_rate + self.slippage_rate)


def study_signals(frames: dict[str, pd.DataFrame], strategy: Strategy, *,
                  costs: CostModel | None = None, max_hold_hours: float = 48,
                  execution_tf: str = "5m", signals: pd.DataFrame | None = None) -> pd.DataFrame:
    costs = costs or CostModel()
    sig = signals if signals is not None else strategy.compute(frames)
    ex = frames[execution_tf]
    times = ex.index
    o, h, lo, c = (ex[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    period = pd.Timedelta(seconds=TF_SECONDS[strategy.base_tf])
    exit_times = (sig.index[sig["exit"].to_numpy()] + period)
    exit_idx = np.sort(times.searchsorted(exit_times))
    max_bars = int(max_hold_hours * 3600 / TF_SECONDS[execution_tf])
    rows = []
    entries = sig[sig["entry"]]
    for t, row in entries.iterrows():
        i = times.searchsorted(t + period)
        if i >= len(times) or times[i] != t + period:
            continue  # no execution bar exactly at signal close (data gap): skip
        if o[i] <= row["stop"] or o[i] >= row["target"]:
            continue
        entry = o[i] * (1 + costs.slippage_rate)
        stop, target = row["stop"], row["target"]
        j_end = min(len(times) - 1, i + max_bars)
        k = exit_idx[exit_idx.searchsorted(i + 1):]
        strat_exit = int(k[0]) if len(k) and k[0] <= j_end else None
        outcome, exit_px, j_exit = "timeout", c[j_end], j_end
        for j in range(i, j_end + 1):
            if strat_exit is not None and j == strat_exit:
                outcome, exit_px, j_exit = "strategy_exit", o[j], j
                break
            if o[j] <= stop and j > i:
                outcome, exit_px, j_exit = "stop", o[j], j
                break
            if lo[j] <= stop:
                outcome, exit_px, j_exit = "stop", stop, j
                break
            if h[j] >= target:
                outcome, exit_px, j_exit = "target", max(target, o[j]) if j > i else target, j
                break
        exit_net = exit_px * (1 - costs.slippage_rate)
        ret = (exit_net * (1 - costs.fee_rate)) / (entry * (1 + costs.fee_rate)) - 1
        rows.append({"signal_time": t, "entry_time": times[i], "exit_time": times[j_exit],
                     "entry": entry, "exit": exit_net, "stop": stop, "target": target,
                     "ret": ret, "outcome": outcome, "win": ret > 0,
                     "regime": row["regime"], "confidence": row["confidence"]})
    return pd.DataFrame(rows).set_index("signal_time") if rows else pd.DataFrame(
        columns=["entry_time", "exit_time", "entry", "exit", "stop", "target", "ret",
                 "outcome", "win", "regime", "confidence"])


def edge_stats(study: pd.DataFrame) -> dict[str, float | int | None]:
    if study.empty:
        return {"n": 0, "win_rate": None, "expectancy": None, "profit_factor": None}
    gains, losses = study.ret[study.ret > 0].sum(), -study.ret[study.ret <= 0].sum()
    return {"n": len(study), "win_rate": float(study.win.mean()),
            "expectancy": float(study.ret.mean()),
            "profit_factor": float(gains / losses) if losses > 0 else None,
            "median_ret": float(study.ret.median())}
