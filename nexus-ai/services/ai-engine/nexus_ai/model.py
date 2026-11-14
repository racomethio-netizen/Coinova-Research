"""Ensemble classifier (XGBoost + logistic regression) with isotonic calibration.

Training data is split in TIME order: the earlier part fits the models, the later part
(after a purge gap) fits the calibrator. Nothing is shuffled across time.
"""

from __future__ import annotations

import hashlib
import io
import pickle  # artifacts are loaded only after sha256 verification against the DB
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 300, "max_depth": 3, "learning_rate": 0.03, "subsample": 0.8,
    "colsample_bytree": 0.8, "min_child_weight": 20, "reg_lambda": 5.0,
    "eval_metric": "logloss", "n_jobs": 4, "random_state": 7, "tree_method": "hist",
}


@dataclass
class EnsembleModel:
    features: list[str]
    xgb_weight: float = 0.7
    calib_fraction: float = 0.2
    purge: int = 48
    xgb: Any = None
    logit: Any = None
    calibrator: IsotonicRegression | None = None
    medians: dict[str, float] = field(default_factory=dict)
    train_info: dict[str, Any] = field(default_factory=dict)

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        return X[self.features].fillna(self.medians)

    def raw_proba(self, X: pd.DataFrame) -> npt.NDArray[np.float64]:
        Xp = self._prep(X)
        p_x = self.xgb.predict_proba(Xp)[:, 1]
        p_l = self.logit.predict_proba(Xp)[:, 1]
        return np.asarray(self.xgb_weight * p_x + (1 - self.xgb_weight) * p_l)

    def predict_proba(self, X: pd.DataFrame) -> npt.NDArray[np.float64]:
        raw = self.raw_proba(X)
        return raw if self.calibrator is None else np.asarray(self.calibrator.predict(raw))

    def fit(self, X: pd.DataFrame, y: pd.Series) -> EnsembleModel:
        n = len(X)
        n_cal = int(n * self.calib_fraction)
        fit_end = n - n_cal - self.purge
        if fit_end < 1000 or n_cal < 500:
            raise ValueError(f"not enough data to train and calibrate (n={n})")
        X_fit, y_fit = X.iloc[:fit_end], y.iloc[:fit_end]
        self.medians = X_fit[self.features].median().to_dict()
        Xf = self._prep(X_fit)
        self.xgb = XGBClassifier(**XGB_PARAMS).fit(Xf, y_fit)
        self.logit = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500)
                                   ).fit(Xf, y_fit)
        X_cal, y_cal = X.iloc[n - n_cal:], y.iloc[n - n_cal:]
        self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
        self.calibrator.fit(self.raw_proba(X_cal), y_cal)
        self.train_info = {"n_fit": int(fit_end), "n_cal": int(n_cal),
                           "base_rate": float(y_fit.mean()),
                           "start": str(X.index[0]), "end": str(X.index[-1])}
        return self

    def importance(self) -> dict[str, float]:
        imp = self.xgb.feature_importances_
        return dict(sorted(zip(self.features, map(float, imp), strict=True),
                           key=lambda kv: -kv[1]))

    # ---------------------------------------------------------------- artifacts
    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump(self, buf, protocol=pickle.HIGHEST_PROTOCOL)
        return buf.getvalue()

    @staticmethod
    def from_bytes(data: bytes, *, expected_sha256: str) -> EnsembleModel:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ValueError("model artifact hash mismatch: refusing to load")
        model = pickle.loads(data)  # noqa: S301 - integrity verified above
        if not isinstance(model, EnsembleModel):
            raise TypeError("artifact is not an EnsembleModel")
        return model
