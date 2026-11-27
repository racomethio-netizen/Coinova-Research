"""Machine-learning FILTER for strategy-module signals (unified brief, section D).

Meta-labelling (Lopez de Prado): a strategy module proposes a trade; a classifier
estimates the probability that this trade ends with a positive net result; the trade is
only allowed when that probability is high enough. The filter can only BLOCK trades, it
never creates one.

* Features (``signal_features``) describe the market at the signal bar's close, using
  only that bar and earlier ones (closed higher-timeframe bars via ``align_higher``).
* Model (``MetaFilter``): the average of LightGBM, XGBoost and CatBoost probabilities,
  with fixed settings (no tuning), then isotonic calibration fitted on the LATER part of
  the training window (time order, never shuffled; trades still open when that part
  starts are purged from the fitting part).
* Drift: ``nexus_ai.drift.psi`` compares a window's features with the training ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies import indicators as ind
from nexus_strategies.frames import align_higher

FEATURES = ("atr_pct", "adx", "rsi", "ema50_dist", "ema20_slope", "range_atr", "rv_day",
            "hour_sin", "hour_cos", "dow", "h1_adx", "h1_rsi", "h1_ema50_dist",
            "h1_atr_rank", "d1_ema50_dist", "d1_atr_pct", "side", "risk_atr", "cost_risk",
            "symbol_code")
SEED = 7


def _ctx(df: pd.DataFrame) -> pd.DataFrame:
    atr = ind.atr(df, 14)
    return pd.DataFrame({
        "adx": ind.adx(df, 14), "rsi": ind.rsi(df["close"], 14),
        "ema50_dist": (df["close"] - ind.ema(df["close"], 50)) / atr,
        "atr_pct": atr / df["close"],
        "atr_rank": (atr / df["close"]).rolling(720, min_periods=100).rank(pct=True),
    }, index=df.index)


def bar_features(frames: dict[str, pd.DataFrame], tf: str) -> pd.DataFrame:
    """Market features for every bar of timeframe ``tf`` (causal)."""
    f = frames[tf]
    c = f["close"]
    atr = ind.atr(f, 14)
    bars_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}[tf]
    out = pd.DataFrame({
        "atr_pct": atr / c, "adx": ind.adx(f, 14), "rsi": ind.rsi(c, 14),
        "ema50_dist": (c - ind.ema(c, 50)) / atr,
        "ema20_slope": ind.ema(c, 20).diff(5) / atr,
        "range_atr": (f["high"] - f["low"]) / atr,
        "rv_day": np.log(c).diff().rolling(max(bars_per_day, 20), min_periods=20).std(),
    }, index=f.index)
    hours = pd.DatetimeIndex(f.index).hour + pd.DatetimeIndex(f.index).minute / 60
    out["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    out["dow"] = pd.DatetimeIndex(f.index).day_of_week
    for ctx_tf, prefix, cols in (("1h", "h1_", ("adx", "rsi", "ema50_dist", "atr_rank")),
                                 ("1d", "d1_", ("ema50_dist", "atr_pct"))):
        if ctx_tf in frames and ctx_tf != tf:
            ctx = _ctx(frames[ctx_tf])[list(cols)]
            out = align_higher(out, tf, ctx, ctx_tf, prefix)
        else:
            for col in cols:
                out[f"{prefix}{col}"] = np.nan
    return out


def signal_features(bars: pd.DataFrame, signals: pd.DataFrame, close: pd.Series,
                    spread: pd.Series | None, symbol_code: int) -> pd.DataFrame:
    """Rows of ``bars`` at the signal bars plus the signal's own terms: side, stop
    distance in ATR, and (forex) the spread as a share of the risk."""
    x = bars.reindex(signals.index).copy()
    risk = (close.reindex(signals.index) - signals["stop"]).abs()
    atr = x["atr_pct"] * close.reindex(signals.index)
    x["side"] = signals["side"].astype(float)
    x["risk_atr"] = risk / atr
    x["cost_risk"] = (spread.reindex(signals.index) / risk) if spread is not None else 0.0
    x["symbol_code"] = float(symbol_code)
    return x[list(FEATURES)]


@dataclass
class MetaFilter:
    threshold: float = 0.40
    models: list[Any] = field(default_factory=list)
    calibrator: Any = None
    info: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _models() -> list[Any]:
        import lightgbm as lgb
        import xgboost as xgb
        from catboost import CatBoostClassifier

        return [
            lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15,
                               min_child_samples=50, subsample=0.8, subsample_freq=1,
                               colsample_bytree=0.8, random_state=SEED, verbose=-1),
            xgb.XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=3,
                              subsample=0.8, colsample_bytree=0.8, random_state=SEED,
                              eval_metric="logloss", n_jobs=2),
            CatBoostClassifier(iterations=300, learning_rate=0.03, depth=4,
                               random_seed=SEED, verbose=0, thread_count=2),
        ]

    def raw(self, x: pd.DataFrame) -> np.ndarray:
        """Each model's probability (columns: LightGBM, XGBoost, CatBoost)."""
        return np.column_stack([m.predict_proba(x.to_numpy(float))[:, 1] for m in self.models])

    def fit(self, x: pd.DataFrame, y: np.ndarray, entry: pd.Series, exit_: pd.Series,
            *, calibration_share: float = 0.2) -> MetaFilter:
        """Fit on the earlier part, calibrate on the later part (time order). Trades that
        were still open when the calibration part starts are dropped from the fit."""
        from sklearn.isotonic import IsotonicRegression

        order = np.argsort(entry.to_numpy())
        x, y = x.iloc[order], np.asarray(y)[order]
        entry, exit_ = entry.iloc[order], exit_.iloc[order]
        cut = int(len(x) * (1 - calibration_share))
        cal_start = entry.iloc[cut]
        fit_mask = (exit_.iloc[:cut] < cal_start).to_numpy()
        xf, yf = x.iloc[:cut][fit_mask], y[:cut][fit_mask]
        self.models = [m.fit(xf.to_numpy(float), yf) for m in self._models()]
        p_cal = self.raw(x.iloc[cut:]).mean(axis=1)
        self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(
            p_cal, y[cut:])
        self.info = {"fit_rows": len(xf), "purged": int((~fit_mask).sum()),
                     "calibration_rows": len(x) - cut, "base_rate": float(np.mean(y))}
        return self

    def probability(self, x: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.calibrator.predict(self.raw(x).mean(axis=1)), dtype=float)

    def allows(self, x: pd.DataFrame) -> np.ndarray:
        return self.probability(x) >= self.threshold
