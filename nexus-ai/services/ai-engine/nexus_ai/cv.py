"""Purged k-fold cross-validation with embargo (Lopez de Prado), for time series.

Each sample i is an interval [start_i, end_i]: the decision bar and the bar where its label
was decided. For a contiguous test fold covering [a, b]:

* a training sample is PURGED if its interval overlaps [a, b] (its label would use test
  prices), i.e. it is kept only if end_i < a or start_i > b + embargo;
* the EMBARGO removes samples that start shortly after the test fold, whose features
  (rolling windows) overlap the test period.

Folds are contiguous in time and never shuffled. Use this only INSIDE a training window
(model choice); the walk-forward test window is never used to choose anything.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import numpy.typing as npt
import pandas as pd


def label_end_times(df: pd.DataFrame, bar: pd.Timedelta) -> pd.Series:
    """End time of each label from ``end_idx``/``pos`` (row positions within a symbol)."""
    bars = (df["end_idx"] - df["pos"]).clip(lower=0)
    return pd.Series(df.index + (bars.to_numpy() + 1) * bar, index=df.index)


DEFAULT_EMBARGO = pd.Timedelta(hours=24)


def purged_kfold(starts: pd.Index, ends: pd.Series, n_splits: int = 3,
                 embargo: pd.Timedelta = DEFAULT_EMBARGO
                 ) -> Iterator[tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]]:
    """Yield (train_positions, test_positions). ``starts`` must be sorted."""
    if not starts.is_monotonic_increasing:
        raise ValueError("samples must be sorted by time")
    n = len(starts)
    if n_splits < 2 or n < n_splits:
        raise ValueError("need at least 2 folds and as many samples")
    s = starts.to_numpy()
    e = ends.to_numpy()
    bounds = np.linspace(0, n, n_splits + 1).astype(int)
    for k in range(n_splits):
        lo, hi = bounds[k], bounds[k + 1]
        test = np.arange(lo, hi)
        a, b = s[lo], s[hi - 1]
        b_end = max(b, e[lo:hi].max())
        keep = (e < a) | (s > b_end + np.timedelta64(embargo))
        train = np.flatnonzero(keep)
        yield train.astype(np.int64), test.astype(np.int64)
