"""AI engine: labels, model integrity, drift, and gates that pass AND fail correctly."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest
from nexus_ai.drift import drift_report, psi
from nexus_ai.labels import triple_barrier
from nexus_ai.model import EnsembleModel
from nexus_ai.validation import walk_forward_ai


def ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_triple_barrier_target_stop_tie_and_unlabeled_tail() -> None:
    warm = [(100.0, 101.0, 99.0, 100.0)] * 20  # ATR -> 2.0
    df = ohlc([*warm, (100, 101, 99, 100), (100, 105, 99.5, 104), (100, 101, 99, 100),
               (100, 101, 99, 100)])
    lab = triple_barrier(df, tp_mult=2, sl_mult=1, horizon=2, fee_rate=0, slippage_rate=0)
    t = 19  # entry at bar 20 open=100, target 104, stop 98
    assert lab.label.iloc[t] == 1 and lab.ret.iloc[t] == pytest.approx(0.04)
    tie = ohlc([*warm, (100, 105, 97, 100), (100, 100, 100, 100), (100, 100, 100, 100)])
    assert triple_barrier(tie, horizon=2, fee_rate=0, slippage_rate=0).label.iloc[19] == 0
    assert lab.label.iloc[-2:].isna().all()  # not enough future: never guessed


def dataset(n: int, signal: float, seed: int = 0) -> tuple[pd.DataFrame, list[str]]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    p = 1 / (1 + np.exp(-(signal * x1 - 0.7)))
    y = (rng.uniform(size=n) < p).astype(float)
    ret = np.where(y == 1, 0.02, -0.0105)
    df = pd.DataFrame({"x1": x1, "x2": x2, "label": y, "ret": ret}, index=idx)
    df["pos"] = np.arange(n)
    df["end_idx"] = df["pos"] + 4
    df["symbol"] = "BTC/USDT"
    return df, ["x1", "x2"]


@pytest.mark.slow
def test_gates_pass_on_real_signal_and_fail_on_noise() -> None:
    strong, feats = dataset(30_000, signal=2.0)
    rep = walk_forward_ai(strong, feats, train_days=120, test_days=60)
    assert rep.passed, rep.failures
    assert rep.stats["mean_auc"] > 0.7 and rep.stats["expectancy"] > 0
    noise, feats = dataset(30_000, signal=0.0, seed=1)
    rep = walk_forward_ai(noise, feats, train_days=120, test_days=60)
    assert not rep.passed
    assert any("AUC" in f for f in rep.failures)


def test_artifact_integrity() -> None:
    df, feats = dataset(6_000, signal=2.0)
    model = EnsembleModel(features=feats).fit(df[feats], df["label"])
    blob = model.to_bytes()
    good = hashlib.sha256(blob).hexdigest()
    loaded = EnsembleModel.from_bytes(blob, expected_sha256=good)
    np.testing.assert_allclose(loaded.predict_proba(df[feats].iloc[:50]),
                               model.predict_proba(df[feats].iloc[:50]))
    with pytest.raises(ValueError, match="hash mismatch"):
        EnsembleModel.from_bytes(blob + b"x", expected_sha256=good)
    p = model.predict_proba(df[feats])
    assert ((p >= 0) & (p <= 1)).all()
    assert model.train_info["n_fit"] + model.train_info["n_cal"] + 48 == len(df)


def test_refuses_tiny_training_sets() -> None:
    df, feats = dataset(500, signal=1.0)
    with pytest.raises(ValueError, match="not enough data"):
        EnsembleModel(features=feats).fit(df[feats], df["label"])


def test_psi_and_drift() -> None:
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(size=5000))
    assert psi(a, pd.Series(rng.normal(size=5000))) < 0.02
    assert psi(a, pd.Series(rng.normal(1.5, 1, size=5000))) > 0.25
    ref = pd.DataFrame({f"f{i}": rng.normal(size=3000) for i in range(5)})
    cur = pd.DataFrame({f"f{i}": rng.normal(2, 1, size=3000) for i in range(5)})
    rep = drift_report(ref, cur, list(ref.columns))
    assert rep.alert and len(rep.drifted) == 5
    calm = drift_report(ref, ref.sample(frac=1, random_state=1), list(ref.columns),
                        predicted=pd.Series([0.6] * 100), realized=pd.Series([1, 0] * 50))
    assert calm.calibration_gap == pytest.approx(0.1) and calm.alert
