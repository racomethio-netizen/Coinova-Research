"""Parameter stability: do the parameters that looked best on past data stay good?

For every walk-forward window the whole parameter grid is scored on the TRAIN slice and
on the following TEST slice (the same causal signal studies the validation uses). The
report answers four questions:

1. **Does in-sample rank carry over?** Spearman rank correlation, across parameter sets,
   between train and test expectancy. Near zero or negative: the ranking is noise.
2. **Probability of backtest overfitting** (Bailey, Borwein, Lopez de Prado and Zhu,
   2017, simplified to walk-forward windows): the share of windows where the parameter
   set chosen on train lands in the BOTTOM half of the grid on test. About 0.5 means the
   choice is no better than random; well above 0.5 means choosing on train is harmful.
3. **Is the choice stable?** How often each value of each parameter is chosen.
4. **Plateau or spike?** For the chosen set, how much worse its one-step neighbours did on
   train. A large drop means a narrow spike that is unlikely to persist.

This is a diagnostic. It never changes a strategy's validation result.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies.base import Strategy

from nexus_backtest.signals import CostModel
from nexus_backtest.walkforward import GRIDS, _slice, compute_studies, make_windows

MIN_TEST_TRADES = 5    # a parameter set needs this many test trades to be ranked


@dataclass
class StabilityReport:
    strategy: str
    combos: int
    windows: list[dict[str, Any]]
    summary: dict[str, Any] = field(default_factory=dict)
    oos: pd.DataFrame = field(default_factory=pd.DataFrame)   # the walk-forward OOS trades


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def neighbours(combos: list[dict[str, float]], i: int, grid: dict[str, list[float]]) -> list[int]:
    """Indices of parameter sets that differ from ``combos[i]`` by one grid step in one
    parameter."""
    base = combos[i]
    out = []
    for j, c in enumerate(combos):
        diffs = [k for k in base if c[k] != base[k]]
        if len(diffs) != 1:
            continue
        k = diffs[0]
        values = grid[k]
        if abs(values.index(c[k]) - values.index(base[k])) == 1:
            out.append(j)
    return out


def analyse(combos: list[dict[str, float]], studies: list[pd.DataFrame],
            windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]],
            grid: dict[str, list[float]], *, min_train_trades: int = 15) -> list[dict[str, Any]]:
    out = []
    for train_lo, split, test_hi in windows:
        train = [_slice(st, train_lo, split) for st in studies]
        test = [_slice(st, split, test_hi) for st in studies]
        # Expectancy per parameter set, only where there are enough trades to read it.
        tr_exp = {i: float(t.ret.mean()) for i, t in enumerate(train)
                  if len(t) >= min_train_trades}
        te_exp = {i: float(t.ret.mean()) for i, t in enumerate(test)
                  if len(t) >= MIN_TEST_TRADES}
        row: dict[str, Any] = {"test_start": str(split.date()), "eligible": len(tr_exp)}
        if not tr_exp:
            out.append(row)
            continue
        best = max(tr_exp, key=lambda i: tr_exp[i])   # the walk-forward choice
        both = [i for i in tr_exp if i in te_exp]
        row["chosen"] = combos[best]
        row["chosen_index"] = best
        row["chosen_train"] = tr_exp[best]
        row["chosen_test"] = te_exp.get(best)
        row["rank_corr"] = _spearman([tr_exp[i] for i in both], [te_exp[i] for i in both])
        ranked = [te_exp[i] for i in both]
        if best in te_exp and len(ranked) >= 2:
            # Share of ranked sets the chosen one beat on test (0 = worst, 1 = best).
            row["chosen_test_percentile"] = float(
                sum(v < te_exp[best] for v in ranked) / (len(ranked) - 1))
        nb = [tr_exp[j] for j in neighbours(combos, best, grid) if j in tr_exp]
        row["neighbour_train_mean"] = float(np.mean(nb)) if nb else None
        row["neighbour_drop"] = (tr_exp[best] - float(np.mean(nb))) if nb else None
        out.append(row)
    return out


def summarise(rows: list[dict[str, Any]], grid: dict[str, list[float]]) -> dict[str, Any]:
    chosen = [r for r in rows if r.get("chosen")]
    pct = [r["chosen_test_percentile"] for r in chosen
           if r.get("chosen_test_percentile") is not None]
    corr = [r["rank_corr"] for r in chosen if r.get("rank_corr") is not None]
    drops = [r["neighbour_drop"] for r in chosen if r.get("neighbour_drop") is not None]
    sets = Counter(json.dumps(r["chosen"], sort_keys=True) for r in chosen)
    return {
        "windows": len(rows), "windows_with_choice": len(chosen),
        "pbo": float(np.mean([p < 0.5 for p in pct])) if pct else None,
        "chosen_test_percentile_mean": float(np.mean(pct)) if pct else None,
        "rank_corr_mean": float(np.mean(corr)) if corr else None,
        "rank_corr_positive_share": float(np.mean([c > 0 for c in corr])) if corr else None,
        "most_chosen_share": (sets.most_common(1)[0][1] / len(chosen)) if chosen else None,
        "distinct_sets_chosen": len(sets),
        "value_frequency": {k: dict(Counter(str(r["chosen"][k]) for r in chosen))
                            for k in grid},
        "neighbour_drop_mean": float(np.mean(drops)) if drops else None,
        "chosen_train_mean": float(np.mean([r["chosen_train"] for r in chosen]))
        if chosen else None,
        "chosen_test_mean": float(np.mean([r["chosen_test"] for r in chosen
                                           if r.get("chosen_test") is not None]))
        if any(r.get("chosen_test") is not None for r in chosen) else None,
    }


def parameter_stability(data: dict[str, dict[str, pd.DataFrame]], strategy_cls: type[Strategy],
                        *, costs: CostModel | None = None, train_days: int = 180,
                        test_days: int = 60, min_train_trades: int = 15,
                        adjust: Callable[[pd.DataFrame, str], pd.DataFrame] | None = None
                        ) -> StabilityReport:
    costs = costs or CostModel()
    combos, studies = compute_studies(data, strategy_cls, costs=costs, adjust=adjust)
    idx = next(iter(data.values()))["5m"].index
    windows = make_windows(idx[0], idx[-1], train_days=train_days, test_days=test_days)
    g = GRIDS[strategy_cls.code]
    rows = analyse(combos, studies, windows, g, min_train_trades=min_train_trades)
    # The same out-of-sample trades walk_forward() judges: each window's chosen set.
    parts = [_slice(studies[r["chosen_index"]], split, hi)
             for r, (_, split, hi) in zip(rows, windows, strict=True) if "chosen_index" in r]
    oos = pd.concat(parts) if parts else pd.DataFrame()
    return StabilityReport(strategy_cls.code, len(combos), rows, summarise(rows, g), oos)


def _finite(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main() -> None:   # pragma: no cover - research CLI
    from nexus_db import make_engine, make_session_factory
    from nexus_strategies.strategies import ALL_STRATEGIES

    from nexus_backtest.data import load_frames
    from nexus_backtest.reports import breakdown

    p = argparse.ArgumentParser(description="Parameter-stability and OOS breakdown report")
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--only", nargs="*")
    a = p.parse_args()
    engine = make_engine(a.db)
    data = load_frames(make_session_factory(engine), "binance", ["BTC/USDT", "ETH/USDT"],
                       ["5m", "15m", "1h", "4h"])
    engine.dispose()
    costs = CostModel(0.0010, 0.0005)
    results = []
    for code, cls in ALL_STRATEGIES.items():
        if (a.only and code not in a.only) or code not in GRIDS:
            continue
        rep = parameter_stability(data, cls, costs=costs)
        results.append(_finite({"strategy": code, "combos": rep.combos,
                                "summary": rep.summary, "windows": rep.windows,
                                "oos_breakdown": breakdown(rep.oos)}))
        Path(a.out).write_text(json.dumps(results, indent=1, default=str))
        s = rep.summary
        print(code, "PBO", s["pbo"], "rank_corr", s["rank_corr_mean"], flush=True)


if __name__ == "__main__":
    main()
