from __future__ import annotations

import numpy as np
import pytest
from nexus_backtest.montecarlo import bootstrap


def test_all_winners_never_lose_and_drawdown_is_zero() -> None:
    res = bootstrap(np.full(50, 0.01), simulations=200)
    assert res.prob_loss == 0.0 and res.max_drawdown_p95 == pytest.approx(0.0)
    assert res.final_return_median == pytest.approx(1.01**50 - 1)


def test_negative_edge_loses_with_high_probability() -> None:
    rng = np.random.default_rng(1)
    rets = np.where(rng.uniform(size=200) < 0.4, 0.02, -0.02)  # expectancy -0.4%
    res = bootstrap(rets, simulations=1000)
    assert res.prob_loss > 0.9
    assert res.max_drawdown_median > 0.1


def test_deterministic_with_seed_and_input_validation() -> None:
    rets = np.linspace(-0.01, 0.02, 40)
    a, b = bootstrap(rets, seed=5), bootstrap(rets, seed=5)
    assert a == b
    with pytest.raises(ValueError):
        bootstrap(np.ones(5))
    with pytest.raises(ValueError):
        bootstrap(np.r_[np.ones(20), np.nan])


def test_equity_conversion_uses_live_sizing() -> None:
    from nexus_backtest.montecarlo import equity_returns_from_trades

    # 2% stop distance at 1% risk -> 50% of equity, capped to 25%.
    out = equity_returns_from_trades(np.array([100.0]), np.array([98.0]), np.array([0.1]))
    assert out[0] == pytest.approx(0.1 * 0.25)
    # 10% stop distance at 1% risk -> 10% of equity.
    out = equity_returns_from_trades(np.array([100.0]), np.array([90.0]), np.array([0.1]))
    assert out[0] == pytest.approx(0.1 * 0.1)
