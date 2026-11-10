"""Models and calibration (research dependencies, imported lazily)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


def fit_model(kind: str, X: Arr, y: NDArray[np.int64], seed: int) -> Any:
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000, random_state=seed))
    elif kind == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            random_state=seed, n_jobs=1, tree_method="hist", eval_metric="logloss",
        )  # fmt: skip
    else:
        raise ValueError(f"unknown model kind {kind!r}")
    model.fit(X, y)
    return model


def predict(model: Any, X: Arr) -> Arr:
    return np.asarray(model.predict_proba(X)[:, 1], dtype=np.float64)


def fit_calibrator(p_raw: Arr, y: NDArray[np.int64]) -> tuple[str, Callable[[Arr], Arr]]:
    """Isotonic with >= 1000 calibration samples, Platt (sigmoid) otherwise."""
    if len(np.unique(y)) < 2:
        return "none", lambda p: p
    if len(y) >= 1000:
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p_raw, y)
        return "isotonic", lambda p: np.asarray(iso.predict(p), dtype=np.float64)
    from sklearn.linear_model import LogisticRegression

    eps = 1e-6
    logit = lambda p: np.log(np.clip(p, eps, 1 - eps) / (1 - np.clip(p, eps, 1 - eps))).reshape(-1, 1)  # noqa: E731
    lr = LogisticRegression(C=1e6, max_iter=2000).fit(logit(p_raw), y)
    return "sigmoid", lambda p: np.asarray(lr.predict_proba(logit(p))[:, 1], dtype=np.float64)


def brier(p: Arr, y: NDArray[np.int64]) -> float:
    return float(np.mean((p - y) ** 2))


def reliability(p: Arr, y: NDArray[np.int64], bins: int = 10) -> tuple[float, list[dict[str, float | int]]]:
    """Expected calibration error and per-bin (mean predicted, observed frequency, count)."""
    edges = np.linspace(0, 1, bins + 1)
    out: list[dict[str, float | int]] = []
    ece = 0.0
    for lo, hi in itertools.pairwise(edges):
        m = (p >= lo) & ((p < hi) | (hi == 1.0))
        if m.sum() == 0:
            continue
        mp, fy = float(p[m].mean()), float(y[m].mean())
        ece += m.sum() / len(p) * abs(mp - fy)
        out.append({"lo": float(lo), "hi": float(hi), "mean_predicted": round(mp, 6), "observed": round(fy, 6), "n": int(m.sum())})
    return float(ece), out


def auc(p: Arr, y: NDArray[np.int64]) -> float | None:
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p))
