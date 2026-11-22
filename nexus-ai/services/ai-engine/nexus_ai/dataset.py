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


def build_dataset_v2(data: dict[str, dict[str, pd.DataFrame]], *, tp_mult: float = 2.0,
                     sl_mult: float = 1.0, horizon: int = 48, fee_rate: float = 0.001,
                     slippage_rate: float = 0.0005, warmup: int = 300
                     ) -> tuple[pd.DataFrame, list[str]]:
    """f2 features + cross-asset context (the other symbol's returns and the rolling
    correlation, same bar, closed bars only) + every Phase D label."""
    from nexus_ai.features import build_features_v2
    from nexus_ai.labels import direction_labels, forward_returns, volatility_targets

    rt = 2 * (fee_rate + slippage_rate)
    closes = {s: f[BASE_TF]["close"] for s, f in data.items()}
    parts = []
    features: list[str] | None = None
    for symbol, frames in data.items():
        X = build_features_v2(frames)
        others = [s for s in data if s != symbol]
        if others:
            oc = closes[others[0]].reindex(X.index)
            r_self = np.log(closes[symbol]).diff()
            r_other = np.log(oc).diff()
            X["x_ret_4"] = np.log(oc / oc.shift(4))
            X["x_ret_16"] = np.log(oc / oc.shift(16))
            X["x_corr_96"] = r_self.rolling(96, min_periods=96).corr(r_other)
        base = frames[BASE_TF]
        lab = triple_barrier(base, tp_mult=tp_mult, sl_mult=sl_mult, horizon=horizon,
                             fee_rate=fee_rate, slippage_rate=slippage_rate)
        fwd = forward_returns(base, (4, 16, 48), round_trip_cost=rt)
        lab = lab.join(fwd).join(volatility_targets(base, horizon))
        lab["direction_16"] = direction_labels(fwd["fwd_16"], band=rt)
        features = list(X.columns)
        df = X.join(lab)
        df["pos"] = np.arange(len(df))
        df["symbol"] = symbol
        df = df.iloc[warmup:]
        df = df[df["label"].notna()]
        parts.append(df)
    assert features is not None
    return pd.concat(parts).sort_index(kind="stable"), features
