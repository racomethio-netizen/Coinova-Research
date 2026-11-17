"""Monte Carlo robustness of a trade sequence.

Bootstraps the realised per-trade returns (sampling with replacement) to build a
distribution of outcomes: final return, maximum drawdown and the probability of a
losing period. This tests whether a result depends on lucky ordering or a few big
winners. It does not create new trades or change the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MonteCarloResult:
    simulations: int
    trades_per_path: int
    final_return_p05: float
    final_return_median: float
    final_return_p95: float
    max_drawdown_median: float
    max_drawdown_p95: float
    prob_loss: float          # share of paths that end below the starting capital


def bootstrap(returns: np.ndarray, *, simulations: int = 2000, seed: int = 0) -> MonteCarloResult:
    """``returns``: fractional net return per trade, in the order they occurred."""
    r = np.asarray(returns, dtype=float)
    if r.size < 10:
        raise ValueError("need at least 10 trades for a Monte Carlo estimate")
    if not np.isfinite(r).all():
        raise ValueError("returns must be finite")
    rng = np.random.default_rng(seed)
    n = r.size
    idx = rng.integers(0, n, size=(simulations, n))
    paths = r[idx]
    equity = np.cumprod(1.0 + paths, axis=1)
    final = equity[:, -1] - 1.0
    peak = np.maximum.accumulate(np.concatenate([np.ones((simulations, 1)), equity], axis=1), axis=1)[:, 1:]
    dd = np.max(1.0 - equity / peak, axis=1)
    return MonteCarloResult(
        simulations=simulations, trades_per_path=n,
        final_return_p05=float(np.percentile(final, 5)),
        final_return_median=float(np.median(final)),
        final_return_p95=float(np.percentile(final, 95)),
        max_drawdown_median=float(np.median(dd)),
        max_drawdown_p95=float(np.percentile(dd, 95)),
        prob_loss=float(np.mean(final < 0)),
    )


def equity_returns_from_trades(entry: np.ndarray, stop: np.ndarray, ret: np.ndarray, *,
                               risk_per_trade: float = 0.01, max_position: float = 0.25) -> np.ndarray:
    """Convert per-trade returns on notional into equity returns at the live sizing rule.

    Position size = min(risk_per_trade / stop_distance, max_position) of equity, so each
    trade's equity impact is ret * size. Using notional returns directly would assume the
    whole account is committed to every trade, which the live system never does.
    """
    entry = np.asarray(entry, float)
    stop = np.asarray(stop, float)
    stop_frac = np.clip((entry - stop) / entry, 1e-9, None)
    size = np.minimum(risk_per_trade / stop_frac, max_position)
    result: np.ndarray = np.asarray(ret, float) * size
    return result
