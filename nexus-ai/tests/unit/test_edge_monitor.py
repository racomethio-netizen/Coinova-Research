"""Edge-decay monitor: detects decay, stays quiet on a stable edge, only tightens."""

from __future__ import annotations

import numpy as np
import pytest
from nexus_risk.edge_monitor import EdgeConfig, EdgeState, cusum_down, drawdown_r, evaluate


def r_series(mean: float, n: int, seed: int = 0, sd: float = 1.2) -> list[float]:
    return list(np.random.default_rng(seed).normal(mean, sd, n))


def test_stable_positive_edge_stays_normal() -> None:
    states = [evaluate(r_series(0.3, 120, seed)).state for seed in range(200)]
    assert sum(s is EdgeState.STOP for s in states) <= 8          # <= 4% false stops
    assert sum(s is EdgeState.NORMAL for s in states) >= 160      # mostly untouched


def test_decay_is_detected_and_stops() -> None:
    good, bad = r_series(0.3, 60, 1), r_series(-0.6, 40, 2)
    assert evaluate(good, ref_mean=0.3).state is EdgeState.NORMAL
    st = evaluate(good + bad, ref_mean=0.3)
    assert st.state is EdgeState.STOP and any("CUSUM" in x for x in st.reasons)
    assert st.multiplier == 0.0


def test_recent_negative_window_reduces() -> None:
    cfg = EdgeConfig(cusum_h=50, reduce_drawdown_r=90, max_drawdown_r=100)  # rolling rule only
    st = evaluate(r_series(0.4, 30, 3) + [-0.8] * 20, cfg=cfg)
    assert st.state is EdgeState.REDUCE and st.multiplier == 0.5


def test_drawdown_rule_acts_before_min_trades() -> None:
    assert evaluate([-1.25] * 8).state is EdgeState.REDUCE       # 10R before 10 trades
    st = evaluate([-2.0] * 8)
    assert st.state is EdgeState.STOP and "drawdown" in st.reasons[0]
    assert evaluate([-1.0] * 3).state is EdgeState.NORMAL


def test_never_loosens_automatically() -> None:
    st = evaluate(r_series(0.5, 60, 4), previous=EdgeState.STOP)
    assert st.state is EdgeState.STOP and "owner" in st.reasons[-1]
    st2 = evaluate(r_series(0.5, 60, 4), previous=EdgeState.REDUCE)
    assert st2.state is EdgeState.REDUCE


def test_helpers() -> None:
    assert drawdown_r([1, -2, -1, 3, -1]) == pytest.approx(3.0)
    assert cusum_down([0.0] * 10, 0.0, 1.0, 0.5) == 0.0
    assert cusum_down([-1.0] * 10, 0.0, 1.0, 0.5) == pytest.approx(5.0)
    assert evaluate([0.2] * 5).reasons[0].startswith("collecting evidence")
