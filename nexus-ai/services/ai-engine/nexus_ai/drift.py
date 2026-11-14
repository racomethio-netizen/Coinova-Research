"""Model drift monitoring: feature drift (PSI) and live calibration/accuracy drift."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PSI_WARN, PSI_ALERT = 0.1, 0.25


def psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float | None:
    """Population stability index using reference-quantile bins."""
    ref, cur = reference.dropna().to_numpy(), current.dropna().to_numpy()
    if len(ref) < bins * 10 or len(cur) < bins * 5:
        return None
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-6, None), np.clip(c, 1e-6, None)
    return float(np.sum((c - r) * np.log(c / r)))


@dataclass(frozen=True)
class DriftReport:
    feature_psi: dict[str, float]
    drifted: list[str]
    calibration_gap: float | None     # mean predicted - realized frequency
    live_brier: float | None
    alert: bool


def drift_report(reference: pd.DataFrame, current: pd.DataFrame, features: list[str], *,
                 predicted: pd.Series | None = None, realized: pd.Series | None = None,
                 max_calibration_gap: float = 0.08) -> DriftReport:
    scores = {f: s for f in features if (s := psi(reference[f], current[f])) is not None}
    drifted = sorted(f for f, s in scores.items() if s >= PSI_ALERT)
    gap = brier = None
    if predicted is not None and realized is not None:
        both = pd.concat([predicted, realized], axis=1).dropna()
        if len(both) >= 30:
            p, y = both.iloc[:, 0].to_numpy(), both.iloc[:, 1].to_numpy()
            gap, brier = float(p.mean() - y.mean()), float(np.mean((p - y) ** 2))
    alert = (bool(drifted) and len(drifted) >= max(3, len(features) // 5)) or (
        gap is not None and abs(gap) > max_calibration_gap)
    return DriftReport(scores, drifted, gap, brier, alert)
