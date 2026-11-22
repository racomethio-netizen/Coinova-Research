"""Model evaluation: classification quality, calibration and NET trading performance.

Trading metrics are computed on non-overlapping trades after fees and slippage. A model
with good classification metrics but non-positive expectancy is not a trading model.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def expected_calibration_error(y: npt.NDArray[np.float64], p: npt.NDArray[np.float64],
                               bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(float(y[m].mean()) - float(p[m].mean()))
    return float(ece)


def classification_metrics(y: npt.NDArray[np.float64], p: npt.NDArray[np.float64],
                           base_rate: float, threshold: float = 0.5) -> dict[str, Any]:
    y = np.asarray(y, dtype=float)
    pred = (p >= threshold).astype(int)
    two = len(set(y.tolist())) > 1
    brier = float(brier_score_loss(y, p))
    brier_ref = float(brier_score_loss(y, np.full(len(y), base_rate)))
    return {
        "n": len(y), "positive_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, p)) if two else None,
        "pr_auc": float(average_precision_score(y, p)) if two else None,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "brier": brier, "brier_ref": brier_ref,
        "brier_skill": 1 - brier / brier_ref if brier_ref > 0 else None,
        "ece": expected_calibration_error(y, p),
    }


def trading_metrics(ret: pd.Series, *, trades_per_year: float | None = None) -> dict[str, Any]:
    """Per-trade net returns (fractions) -> expectancy, PF, drawdown, Sharpe, Sortino."""
    r = ret.dropna().astype(float)
    n = len(r)
    if n == 0:
        return {"trades": 0, "expectancy": None, "win_rate": None, "profit_factor": None,
                "max_drawdown": None, "sharpe": None, "sortino": None, "t_stat": None,
                "net_return": None}
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    equity = (1 + r).cumprod()
    dd = float((equity / equity.cummax() - 1).min())
    sd = float(r.std()) if n > 1 else 0.0
    down = r[r < 0]
    dsd = float(math.sqrt((down ** 2).mean())) if len(down) else 0.0
    ann = math.sqrt(trades_per_year) if trades_per_year else 1.0
    return {
        "trades": n, "expectancy": float(r.mean()), "win_rate": float((r > 0).mean()),
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "net_return": float(equity.iloc[-1] - 1), "max_drawdown": dd,
        "sharpe": float(r.mean() / sd * ann) if sd > 0 else None,
        "sortino": float(r.mean() / dsd * ann) if dsd > 0 else None,
        "t_stat": float(r.mean() / (sd / math.sqrt(n))) if n > 1 and sd > 0 else None,
    }


def by_group(trades: pd.DataFrame, col: str) -> dict[str, dict[str, Any]]:
    out = {}
    for key, g in trades.groupby(col):
        m = trading_metrics(g["ret"])
        out[str(key)] = {"trades": m["trades"], "expectancy": m["expectancy"],
                         "profit_factor": m["profit_factor"]}
    return out
