"""Walk-forward optimization and independent validation of strategies.

For each window: choose parameters on the TRAIN slice only (maximize expectancy with a
minimum trade count), then record the chosen parameters' signals on the following TEST
slice. Only test-slice (out-of-sample) results are used to judge the strategy.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from nexus_strategies.base import Strategy

from nexus_backtest.signals import CostModel, edge_stats, study_signals

GRIDS: dict[str, dict[str, list[float]]] = {
    "trend_following": {"adx_min": [15, 20, 25], "stop_atr": [1.5, 2.0, 3.0],
                        "target_atr": [3.0, 4.5, 6.0], "use_exit": [0, 1]},
    "momentum": {"rsi_cross": [55, 60], "vol_z_min": [1.0, 2.0], "stop_atr": [1.5, 2.5],
                 "target_atr": [3.0, 4.5], "use_exit": [0, 1]},
    "breakout": {"channel": [20, 48], "squeeze_pct": [0.2, 0.35], "vol_z_min": [1.0, 2.0],
                 "stop_atr": [1.5, 2.5], "target_atr": [3.0, 5.0], "use_exit": [0, 1]},
    "mean_reversion": {"rsi_max": [25, 30], "h_adx_max": [18, 25], "stop_atr": [1.5, 2.5],
                       "use_exit": [0, 1]},
}


@dataclass(frozen=True)
class Gates:
    min_trades: int = 30
    min_expectancy: float = 0.0          # mean net return per trade (fraction)
    min_profit_factor: float = 1.15
    min_positive_windows: float = 0.55
    min_t_stat: float = 1.0


@dataclass
class WalkForwardReport:
    strategy: str
    windows: list[dict[str, Any]]
    oos: pd.DataFrame
    stats: dict[str, Any]
    passed: bool
    failures: list[str] = field(default_factory=list)


def grid(strategy_cls: type[Strategy]) -> list[dict[str, float]]:
    g = GRIDS[strategy_cls.code]
    keys = list(g)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*g.values())]


def make_windows(start: pd.Timestamp, end: pd.Timestamp, *, train_days: int, test_days: int
                 ) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    out = []
    t = start + pd.Timedelta(days=train_days)
    while t + pd.Timedelta(days=test_days) <= end:
        out.append((t - pd.Timedelta(days=train_days), t, t + pd.Timedelta(days=test_days)))
        t += pd.Timedelta(days=test_days)
    return out


def _slice(df: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    # A trade belongs to the window where it was ENTERED, and must also EXIT before the
    # window ends so train outcomes never use test-period prices.
    if df.empty:
        return df
    return df[(df.entry_time >= lo) & (df.exit_time < hi)]


def walk_forward(data: dict[str, dict[str, pd.DataFrame]], strategy_cls: type[Strategy], *,
                 costs: CostModel | None = None, train_days: int = 180, test_days: int = 60,
                 min_train_trades: int = 15, gates: Gates | None = None) -> WalkForwardReport:
    costs = costs or CostModel()
    gates = gates or Gates()
    combos = grid(strategy_cls)
    # Outcomes for every parameter set on the full history (signals are causal).
    studies: list[pd.DataFrame] = []
    for params in combos:
        strat = strategy_cls(**params, round_trip_cost=costs.round_trip)
        parts = [study_signals(frames, strat, costs=costs).assign(symbol=sym)
                 for sym, frames in data.items()]
        studies.append(pd.concat(parts).sort_values("entry_time"))
    any_frames = next(iter(data.values()))
    idx = any_frames["5m"].index
    windows = make_windows(idx[0], idx[-1], train_days=train_days, test_days=test_days)

    records, oos_parts = [], []
    for train_lo, split, test_hi in windows:
        best_i, best_score = None, -math.inf
        for i, st in enumerate(studies):
            tr = _slice(st, train_lo, split)
            if len(tr) < min_train_trades:
                continue
            score = tr.ret.mean()
            if score > best_score:
                best_i, best_score = i, score
        if best_i is None:
            records.append({"train_start": train_lo, "test_start": split, "test_end": test_hi,
                            "params": None, "train_expectancy": None, "test": edge_stats(
                                pd.DataFrame())})
            continue
        test = _slice(studies[best_i], split, test_hi)
        oos_parts.append(test.assign(window=str(split.date())))
        records.append({"train_start": train_lo, "test_start": split, "test_end": test_hi,
                        "params": combos[best_i], "train_expectancy": best_score,
                        "train_n": len(_slice(studies[best_i], train_lo, split)),
                        "test": edge_stats(test)})
    oos = pd.concat(oos_parts) if oos_parts else pd.DataFrame(columns=["ret", "win"])
    stats: dict[str, Any] = dict(edge_stats(oos))
    traded = [r for r in records if r["test"]["n"]]
    positive = [r for r in traded if (r["test"]["expectancy"] or 0) > 0]
    stats["windows"] = len(records)
    stats["windows_traded"] = len(traded)
    stats["positive_window_share"] = len(positive) / len(traded) if traded else None
    if len(oos) > 1 and oos.ret.std() > 0:
        stats["t_stat"] = float(oos.ret.mean() / (oos.ret.std() / math.sqrt(len(oos))))
    else:
        stats["t_stat"] = None

    failures = []
    if stats["n"] < gates.min_trades:
        failures.append(f"OOS trades {stats['n']} < {gates.min_trades}")
    if stats["expectancy"] is None or stats["expectancy"] <= gates.min_expectancy:
        failures.append(f"OOS expectancy {stats['expectancy']} <= {gates.min_expectancy}")
    if stats["profit_factor"] is None or stats["profit_factor"] < gates.min_profit_factor:
        failures.append(f"OOS profit factor {stats['profit_factor']} < {gates.min_profit_factor}")
    if (stats["positive_window_share"] or 0) < gates.min_positive_windows:
        failures.append(f"positive windows {stats['positive_window_share']} < "
                        f"{gates.min_positive_windows}")
    if stats["t_stat"] is None or stats["t_stat"] < gates.min_t_stat:
        failures.append(f"t-stat {stats['t_stat']} < {gates.min_t_stat}")
    return WalkForwardReport(strategy_cls.code, records, oos, stats, not failures, failures)
