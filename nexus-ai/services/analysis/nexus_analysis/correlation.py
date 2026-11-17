"""Rolling correlation of log returns against a reference series (BTC for alts)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_correlation(asset_close: pd.Series, reference_close: pd.Series,
                        window: int = 96) -> pd.Series:
    """Causal rolling Pearson correlation of log returns, aligned on timestamps.
    NaN until ``window`` aligned returns exist."""
    a = np.log(asset_close).diff()
    b = np.log(reference_close).diff()
    both = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    corr = both["a"].rolling(window, min_periods=window).corr(both["b"])
    return corr.reindex(asset_close.index)
