"""Optional ML signal-quality filter for NQS-1.

A logistic model estimates the probability that a setup ends with a profit, from the
features known at entry: ADX, RSI, volatility and band-width percentiles, momentum,
MACD histogram, higher-timeframe ADX, confirmation count, distance from EMA 20, and the
strategy and regime.

Rules:
* **Chronological only.** It is trained on trades that CLOSED before the test period
  starts, so no outcome from the test period leaks in.
* **It can only veto.** It refuses a trade whose estimated win probability is below the
  break-even probability for that trade's reward-to-risk, plus a margin. It never
  enlarges a position and never bypasses a risk control.
* It needs at least ``MIN_TRADES`` training trades; otherwise it is not used.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from nexus_strategies.nqs1.regime import REGIMES
from nexus_strategies.nqs1.strategies import STRATEGIES

FEATURES = ("adx", "rsi", "vol_pct", "width_pct", "mom", "macd_hist", "htf_adx", "conf",
            "dist_ema20")
MIN_TRADES = 150
MARGIN = 0.02


def vector(features: dict[str, float], strategy: str, regime: str) -> list[float]:
    x = [float(features.get(k, 0.0)) for k in FEATURES]
    x = [0.0 if not np.isfinite(v) else v for v in x]
    x += [1.0 if strategy == s else 0.0 for s in STRATEGIES]
    x += [1.0 if regime == r else 0.0 for r in REGIMES]
    return x


def train(trades: list[dict[str, Any]]) -> Any | None:
    """Fit on closed trades (each with ``features``, ``strategy``, ``regime``, ``r``)."""
    if len(trades) < MIN_TRADES:
        return None
    y = np.array([1 if t["r"] > 0 else 0 for t in trades])
    if y.min() == y.max():
        return None
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x = np.array([vector(t["features"], t["strategy"], t["regime"]) for t in trades])
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))
    model.fit(x, y)
    return model


def make_veto(model: Any) -> Callable[[Any, float], bool]:
    """True = refuse. Break-even win probability for reward R is 1 / (1 + R)."""
    def veto(pos: Any, target_r: float) -> bool:
        p = float(model.predict_proba(np.array([vector(pos.features, pos.strategy,
                                                       pos.regime)]))[0, 1])
        return p < 1 / (1 + max(target_r, 1e-9)) + MARGIN
    return veto
