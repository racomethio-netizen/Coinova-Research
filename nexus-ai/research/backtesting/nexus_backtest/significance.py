"""Statistical checks for strategy results.

* ``stationary_bootstrap_pvalue``: Jesse's rule-significance test
  (jesse/research/rule_significance_testing). The returns are centred on zero (the null:
  no edge) and resampled in random-length blocks (geometric, mean ``block``), which keeps
  short-range dependence. p = share of resampled means >= the observed mean.
* ``sqn``: Van Tharp's System Quality Number, as in Backtrader's SQN analyzer:
  sqrt(N) * mean / stdev of trade returns. Not meaningful below 30 trades.
* ``holm``: Holm-Bonferroni step-down correction across many tests (the library runs 24),
  so that one lucky test cannot pass by chance.
* ``trade_shuffle_drawdowns``: Monte Carlo of the trade order (Jesse's monte_carlo_trades):
  the distribution of maximum drawdown when the same trades arrive in another order.
"""

from __future__ import annotations

import math

import numpy as np


def stationary_bootstrap_pvalue(returns: np.ndarray, *, simulations: int = 5000,
                                block: float = 10.0, seed: int = 0) -> float | None:
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return None
    observed = float(x.mean())
    centred = x - observed
    rng = np.random.default_rng(seed)
    p_restart = 1.0 / max(1.0, block)
    idx = np.empty(n, dtype=np.int64)
    hits = 0
    for _ in range(simulations):
        restart = rng.random(n) < p_restart
        restart[0] = True
        starts = rng.integers(0, n, size=n)
        cur = 0
        for i in range(n):
            cur = starts[i] if restart[i] else (cur + 1) % n
            idx[i] = cur
        if centred[idx].mean() >= observed:
            hits += 1
    return (hits + 1) / (simulations + 1)


def sqn(returns: np.ndarray) -> float | None:
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return None
    return float(math.sqrt(len(x)) * x.mean() / x.std(ddof=1))


def holm(pvalues: dict[str, float | None], alpha: float = 0.05) -> dict[str, bool]:
    """Which hypotheses are rejected (edge significant) after Holm's correction. A test
    without a p-value (too few trades) is never significant but still counts in m."""
    m = len(pvalues)
    ranked = sorted(((p if p is not None else 1.0), k) for k, p in pvalues.items())
    out = dict.fromkeys(pvalues, False)
    for i, (p, k) in enumerate(ranked):
        if p > alpha / (m - i):
            break
        out[k] = True
    return out


def trade_shuffle_drawdowns(pnl: np.ndarray, start_equity: float, *, simulations: int = 2000,
                            seed: int = 0) -> dict[str, float] | None:
    x = np.asarray(pnl, dtype=float)
    if len(x) < 10 or start_equity <= 0:
        return None
    rng = np.random.default_rng(seed)
    dds = np.empty(simulations)
    for j in range(simulations):
        eq = start_equity + np.cumsum(rng.permutation(x))
        peak = np.maximum.accumulate(np.r_[start_equity, eq])[1:]
        dds[j] = float(np.max((peak - eq) / peak))
    return {"median": float(np.median(dds)), "p95": float(np.percentile(dds, 95))}
