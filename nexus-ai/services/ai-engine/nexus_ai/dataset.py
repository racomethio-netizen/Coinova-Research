"""Assemble the supervised dataset (features + triple-barrier labels) across symbols."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_ai.features import BASE_TF, build_features
from nexus_ai.labels import triple_barrier


def build_dataset(data: dict[str, dict[str, pd.DataFrame]], *, tp_mult: float = 2.0,
                  sl_mult: float = 1.0, horizon: int = 48, fee_rate: float = 0.001,
                  slippage_rate: float = 0.0005, warmup: int = 300) -> tuple[pd.DataFrame, list[str]]:
    parts = []
    features: list[str] | None = None
    for symbol, frames in data.items():
        X = build_features(frames)
        lab = triple_barrier(frames[BASE_TF], tp_mult=tp_mult, sl_mult=sl_mult, horizon=horizon,
                             fee_rate=fee_rate, slippage_rate=slippage_rate)
        features = list(X.columns)
        df = X.join(lab)
        df["pos"] = np.arange(len(df))
        df["symbol"] = symbol
        df = df.iloc[warmup:]
        df = df[df["label"].notna()]
        parts.append(df)
    assert features is not None
    full = pd.concat(parts).sort_index(kind="stable")
    return full, features
