"""Binance Spot Grid simulator on hand-made 5-minute bars: lines, the start purchase, a
full trade, the opposite order waiting one bar, trade-through fills, the stop, the 30-day
restart, the holding benchmark and the statistics."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import binance_grid_bt as bg

T0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
CFG = bg.GridCfg("test", 0.10, 4, False)


def bars(rows: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([T0 + 300 * i for i in range(len(rows))], dtype=np.int64)
    return t, np.array(rows, dtype=float)


def flat(n: int, px: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(px, px, px, px)] * n


def test_lines_are_geometric_and_span_the_range():
    L = bg.lines(100.0, CFG)
    assert len(L) == 5 and L[0] == pytest.approx(90) and L[-1] == pytest.approx(110)
    ratios = [b / a for a, b in itertools.pairwise(L)]
    assert max(ratios) == pytest.approx(min(ratios))


def test_start_buys_the_grids_above_and_uses_the_whole_investment():
    t, o = bars(flat(3))
    res = bg.simulate(t, o, CFG, fee=0.0, slip=0.0)
    # Nothing moved and costs are zero, so equity is unchanged.
    assert res["daily"].iloc[-1] == pytest.approx(1000.0)
    assert res["trips"] == 0 and res["bots"] == 1
    t, o = bars(flat(3))
    res = bg.simulate(t, o, CFG)                       # with costs: initial buy + final sale
    assert 990 < res["daily"].iloc[-1] < 1000


def one_trip_rows() -> list[tuple[float, float, float, float]]:
    return [(100, 100, 100, 100),
            (100, 100, 99.0, 99.2),                    # down: the buy at 99.49 fills
            (99.2, 105, 99.2, 105),                    # up: its sell at 104.61 fills
            (105, 105, 105, 105)]


def test_a_full_grid_trade_earns_one_step_minus_fees():
    t, o = bars(one_trip_rows())
    res = bg.simulate(t, o, CFG, fee=0.0, slip=0.0)
    L = bg.lines(100.0, CFG)
    q = 1000 / (L[0] + L[1] + L[2] + 100.0)           # 3 buy lines + 1 grid bought at 100
    assert res["trips"] == 1
    # Final: the trip profit plus the held grid revalued from 100 to 105 (then sold).
    assert res["daily"].iloc[-1] == pytest.approx(1000 + q * (L[3] - L[2]) + q * 5)


def test_the_opposite_order_waits_for_the_next_bar():
    t, o = bars([(100, 100, 100, 100), (100, 105, 99.0, 104.0), (104, 104, 104, 104)])
    assert bg.simulate(t, o, CFG, fee=0.0, slip=0.0)["trips"] == 0


def test_touching_a_line_is_not_a_fill():
    L = bg.lines(100.0, CFG)
    t, o = bars([(100, 100, 100, 100), (100, 100, L[2], 100), (100, 105, 100, 105)])
    res = bg.simulate(t, o, CFG, fee=0.0, slip=0.0)
    assert res["trips"] == 0


def test_stop_sells_everything_and_the_bot_waits():
    cfg = bg.GridCfg("stop", 0.10, 4, True)
    rows = [(100, 100, 100, 100), (100, 100, 85.0, 86.0), (86, 110, 86, 110), *flat(3, 110)]
    t, o = bars(rows)
    res = bg.simulate(t, o, cfg, fee=0.0, slip=0.0)
    assert res["stops"] == 1 and res["trips"] == 0
    L = bg.lines(100.0, cfg)
    q = 1000 / (L[0] + L[1] + L[2] + 100.0)
    stop_px = L[0] * 0.95
    # All 4 grids end up holding coins (3 bought on the way down), sold at the stop.
    expected = 1000 - q * (L[0] + L[1] + L[2] + 100.0) + 4 * q * stop_px
    assert res["daily"].iloc[-1] == pytest.approx(expected)
    nostop = bg.simulate(t, o, CFG, fee=0.0, slip=0.0)
    assert nostop["stops"] == 0 and nostop["daily"].iloc[-1] > expected  # rebounded


def test_a_new_bot_every_30_days_closes_the_old_one():
    n = 31 * 288                                      # 31 days of 5-minute bars
    t, o = bars(flat(n))
    res = bg.simulate(t, o, CFG)
    assert res["bots"] == 2
    one = bg.simulate(*bars(flat(3)), CFG)["daily"].iloc[-1]
    assert res["daily"].iloc[-1] < one                # paid the restart costs twice


def test_hold_and_stats():
    t, o = bars([(100, 100, 100, 100), *flat(288 * 2, 110)])
    h = bg.hold(t, o, fee=0.0, slip=0.0)
    assert h.iloc[-1] == pytest.approx(1100)
    idx = pd.to_datetime(["2024-03-01", "2024-09-01", "2025-03-01"], utc=True)
    s = bg.stats(pd.Series([1100.0, 990.0, 1188.0], index=idx), 1000.0)
    assert s["half_years"]["2024-H1"] == pytest.approx(0.10)
    assert s["half_years"]["2024-H2"] == pytest.approx(-0.10)
    assert s["max_drawdown"] == pytest.approx(-0.10)
    assert s["by_year"] == {"2024": pytest.approx(-0.01), "2025": pytest.approx(0.2)}


def test_gates():
    m = {"net_return": 0.2, "sharpe": 0.8, "calmar": 0.5, "half_years": {"a": 0.1, "b": 0.1}}
    hold_m = {"sharpe": 0.6, "calmar": 0.4}
    g = bg.gates(m, hold_m, {"net_return": 0.05}, 0.1, True)
    assert all(g.values())
    g = bg.gates(m | {"sharpe": 0.55}, hold_m | {"sharpe": 0.7}, {"net_return": 0.05}, 0.1, True)
    assert not g["sharpe_above_holding"]
