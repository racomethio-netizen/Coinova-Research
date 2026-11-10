"""Validation pipeline: leakage-free splits, learnability, calibration, determinism, dataset causality."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from synthetic import aggregate, random_walk

from nexus_engine.signals.strategy import Params
from nexus_engine.validation.dataset import FEATURES, Dataset, build_dataset
from nexus_engine.validation.splits import walk_forward
from nexus_engine.validation.walkforward import WalkForwardConfig, run_walk_forward

T0 = datetime(2024, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 5000), st.integers(1, 200)), min_size=20, max_size=300), st.integers(2, 6), st.integers(0, 48))
def test_splits_never_leak(raw: list[tuple[int, int]], n_folds: int, embargo_h: int) -> None:
    raw.sort()
    t = [T0 + H * a for a, _ in raw]
    t_end = [T0 + H * (a + d) for a, d in raw]
    folds, lockbox, lock_start = walk_forward(t, t_end, n_folds=n_folds, embargo=timedelta(hours=embargo_h))
    lock = set(lockbox.tolist())
    for f in folds:
        train = np.concatenate([f.fit, f.calib])
        assert lock.isdisjoint(train.tolist()) and lock.isdisjoint(f.test.tolist())
        for i in train:
            assert t[i] < f.test_start  # chronological
            assert t_end[i] < f.test_start  # purged: no label uses test-period prices
        for i in f.test:
            assert f.test_start <= t[i] <= f.test_end
        if len(f.calib) and len(f.fit):
            assert max(t_end[i] for i in f.fit) < t[f.calib[0]]  # fit labels end before calibration starts
    if lock_start is not None:
        for f in folds:
            assert all(t_end[i] < lock_start for i in np.concatenate([f.fit, f.calib, f.test]))


def planted(n: int = 3000, seed: int = 0) -> Dataset:
    """Synthetic dataset where feature 0 genuinely predicts the label."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, len(FEATURES)))
    X[:, FEATURES.index("net_rr")] = rng.uniform(1.0, 3.0, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-2.0 * X[:, 0]))).astype(np.int64)
    t = tuple(T0 + H * (3 * i) for i in range(n))
    t_end = tuple(ti + H * int(rng.integers(1, 24)) for ti in t)
    ret = np.where(y == 1, 0.02, -0.01).astype(np.float64)
    return Dataset(X, y, t, t_end, ret, X[:, FEATURES.index("net_rr")].copy())


@pytest.mark.parametrize("model", ["logistic", "xgboost"])
def test_learns_a_real_signal_out_of_sample_and_is_calibrated(model: str) -> None:
    rep = run_walk_forward(planted(), WalkForwardConfig(model=model))
    oos = rep["out_of_sample"]
    assert oos["samples"] > 1000 and oos["auc"] > 0.75 and oos["ece"] < 0.08
    assert all(f["status"] == "ok" for f in rep["folds"])
    assert rep["lockbox"]["evaluated"] is False  # never touched unless explicitly requested


def test_no_signal_means_no_skill() -> None:
    ds = planted()
    noise = Dataset(np.random.default_rng(9).normal(size=ds.X.shape), ds.y, ds.t, ds.t_end, ds.net_return, ds.net_rr)
    assert 0.4 < run_walk_forward(noise, WalkForwardConfig())["out_of_sample"]["auc"] < 0.6


@pytest.mark.parametrize("model", ["logistic", "xgboost"])
def test_reruns_are_identical(model: str) -> None:
    cfg = WalkForwardConfig(model=model)
    a = run_walk_forward(planted(1500), cfg, evaluate_lockbox=True)
    b = run_walk_forward(planted(1500), cfg, evaluate_lockbox=True)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["lockbox"]["evaluated"] is True and a["lockbox"]["result"]["n_test"] == 300


def test_dataset_is_causal_and_labels_are_well_formed() -> None:
    candles = random_walk("1h", 1400, 71)
    start, end = candles[600].close_time, candles[-1].close_time
    full = build_dataset("1h", candles, aggregate(candles, "1h", "4h"), start, end, Params())
    assert len(full) > 0 and set(full.y.tolist()) <= {0, 1} and np.isfinite(full.X).all()
    assert list(full.t) == sorted(full.t) and all(e > s for s, e in zip(full.t, full.t_end, strict=True))
    cut = candles[:1100]
    part = build_dataset("1h", cut, aggregate(cut, "1h", "4h"), start, end, Params())
    by_t = {t: i for i, t in enumerate(full.t)}
    assert len(part) > 0
    for j, t in enumerate(part.t):  # samples resolvable with less data are identical with more data
        i = by_t[t]
        np.testing.assert_array_equal(part.X[j], full.X[i])
        assert part.y[j] == full.y[i] and part.t_end[j] == full.t_end[i]
