"""Chronological walk-forward splits with purging, embargo, and a lockbox.

- Folds: the development period is cut into n_folds+1 time blocks; fold k trains on
  everything before block k and tests on block k (expanding window).
- Purging: a training sample whose label window [t, t_end] reaches into the test
  block is dropped (its label uses test-period prices).
- Embargo: training samples starting within `embargo` after a test block are dropped.
- Calibration slice: the most recent `calib_frac` of each training set, purged
  against the fit set the same way.
- Lockbox: the most recent `lockbox_frac` of samples is excluded from every fold and
  evaluated once, at approval time.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray

Idx = NDArray[np.int64]


@dataclass(frozen=True)
class Fold:
    fit: Idx
    calib: Idx
    test: Idx
    test_start: datetime
    test_end: datetime


def _purge(train: Idx, t: list[datetime], t_end: list[datetime], start: datetime, end: datetime, embargo: timedelta) -> Idx:
    keep = [i for i in train if not (t_end[i] >= start and t[i] < end) and not (end <= t[i] < end + embargo)]
    return np.array(keep, dtype=np.int64)


def walk_forward(
    t: list[datetime],
    t_end: list[datetime],
    n_folds: int = 4,
    lockbox_frac: float = 0.2,
    calib_frac: float = 0.2,
    embargo: timedelta = timedelta(0),
) -> tuple[list[Fold], Idx, datetime | None]:
    """Returns (folds, lockbox indices, lockbox start). Samples must be sorted by t."""
    n = len(t)
    if any(b < a for a, b in itertools.pairwise(t)):
        raise ValueError("samples must be sorted by time")
    if n == 0:
        return [], np.array([], dtype=np.int64), None
    lock_start_i = int(n * (1 - lockbox_frac))
    lock_start = t[lock_start_i] if lockbox_frac > 0 and lock_start_i < n else None
    dev = np.arange(lock_start_i if lock_start else n, dtype=np.int64)
    # The lockbox is purged from development data like any test block.
    if lock_start is not None:
        dev = np.array([i for i in dev if t_end[i] < lock_start], dtype=np.int64)
    lockbox = np.arange(lock_start_i, n, dtype=np.int64) if lock_start else np.array([], dtype=np.int64)
    if len(dev) == 0:
        return [], lockbox, lock_start
    t0, t1 = t[dev[0]], t[dev[-1]]
    edges = [t0 + (t1 - t0) * k / (n_folds + 1) for k in range(n_folds + 2)]
    folds: list[Fold] = []
    for k in range(1, n_folds + 1):
        start, end = edges[k], edges[k + 1]
        test = np.array([i for i in dev if start <= t[i] < end or (k == n_folds and t[i] == end)], dtype=np.int64)
        train = _purge(np.array([i for i in dev if t[i] < start], dtype=np.int64), t, t_end, start, end, embargo)
        if len(test) == 0 or len(train) < 2:
            continue
        cut = int(len(train) * (1 - calib_frac))
        calib = train[cut:]
        fit = train[:cut]
        if len(calib):
            fit = _purge(fit, t, t_end, t[calib[0]], t_end[calib[-1]], timedelta(0))
        folds.append(Fold(fit, calib, test, start, end))
    return folds, lockbox, lock_start
