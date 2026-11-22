"""Model candidates (Phase D) behind one interface, with time-ordered calibration.

Families: ``logit`` (baseline), ``xgb``, ``lgbm``, ``catboost``. LightGBM and CatBoost are
research-only dependencies (``pip install .[research]``); a missing one is reported, never
faked. Every model:

* fills missing values with TRAINING medians (never statistics from later data);
* is fitted on the earlier part of its training data and calibrated (isotonic) on the
  later part, after dropping fit rows whose label ends inside the calibration period;
* never shuffles time.

Uncertainty: ``disagreement`` is the spread of calibrated probabilities across families.
The families share the same features and labels, so they are NOT independent votes; the
spread is used only as a reason to abstain, never as extra confidence. ``OodDetector``
(Isolation Forest on training features) flags inputs unlike anything seen in training.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FAMILIES = ("logit", "xgb", "lgbm", "catboost")
SEED = 7


def available_families() -> list[str]:
    out = ["logit", "xgb"]
    if importlib.util.find_spec("lightgbm"):
        out.append("lgbm")
    if importlib.util.find_spec("catboost"):
        out.append("catboost")
    return out


def make_estimator(family: str) -> Any:
    if family == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
    if family == "xgb":
        from xgboost import XGBClassifier

        return XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8,
                             colsample_bytree=0.8, min_child_weight=20, reg_lambda=5.0,
                             eval_metric="logloss", n_jobs=4, random_state=SEED,
                             tree_method="hist")
    if family == "lgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(n_estimators=300, num_leaves=15, learning_rate=0.03,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              min_child_samples=100, reg_lambda=5.0, random_state=SEED,
                              n_jobs=4, verbose=-1)
    if family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05, l2_leaf_reg=5,
                                  random_seed=SEED, verbose=False, thread_count=4,
                                  allow_writing_files=False)
    raise ValueError(f"unknown model family {family}")


@dataclass
class CalibratedModel:
    family: str
    features: list[str]
    calib_fraction: float = 0.2
    est: Any = None
    calibrator: IsotonicRegression | None = None
    medians: dict[str, float] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        return X[self.features].fillna(self.medians).fillna(0.0)

    def raw(self, X: pd.DataFrame) -> npt.NDArray[np.float64]:
        return np.asarray(self.est.predict_proba(self._prep(X))[:, 1], dtype=float)

    def predict_proba(self, X: pd.DataFrame) -> npt.NDArray[np.float64]:
        r = self.raw(X)
        return r if self.calibrator is None else np.asarray(self.calibrator.predict(r))

    def fit(self, X: pd.DataFrame, y: pd.Series, ends: pd.Series) -> CalibratedModel:
        n = len(X)
        cut = n - int(n * self.calib_fraction)
        cal_start = X.index[cut]
        fit_mask = np.asarray(ends.iloc[:cut] < cal_start)          # purge overlap
        X_fit, y_fit = X.iloc[:cut][fit_mask], y.iloc[:cut][fit_mask]
        if len(X_fit) < 1000 or n - cut < 300 or y_fit.nunique() < 2:
            raise ValueError(f"not enough data to fit and calibrate (n={n})")
        self.medians = X_fit[self.features].median().to_dict()
        self.est = make_estimator(self.family).fit(self._prep(X_fit), y_fit.astype(int))
        self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
        self.calibrator.fit(self.raw(X.iloc[cut:]), y.iloc[cut:].astype(int))
        self.info = {"n_fit": len(X_fit), "n_cal": n - cut, "base_rate": float(y_fit.mean()),
                     "start": str(X.index[0]), "end": str(X.index[-1])}
        return self


def disagreement(probas: list[npt.NDArray[np.float64]]) -> npt.NDArray[np.float64]:
    return np.asarray(np.std(np.vstack(probas), axis=0))


@dataclass
class OodDetector:
    """Isolation Forest on training features; ``flag`` marks rows scoring below the
    ``quantile`` of training scores (i.e. less typical than 99% of training rows)."""

    features: list[str]
    quantile: float = 0.01
    forest: Any = None
    threshold: float = 0.0
    medians: dict[str, float] = field(default_factory=dict)

    def fit(self, X: pd.DataFrame) -> OodDetector:
        self.medians = X[self.features].median().to_dict()
        Xp = X[self.features].fillna(self.medians).fillna(0.0)
        self.forest = IsolationForest(n_estimators=200, max_samples=min(4096, len(Xp)),
                                      random_state=SEED, n_jobs=4).fit(Xp)
        self.threshold = float(np.quantile(self.forest.score_samples(Xp), self.quantile))
        return self

    def flag(self, X: pd.DataFrame) -> npt.NDArray[np.bool_]:
        Xp = X[self.features].fillna(self.medians).fillna(0.0)
        return np.asarray(self.forest.score_samples(Xp) < self.threshold)
