"""Backtests: production parity, accounting invariants, risk rules, metrics, baselines."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import numpy as np
import pytest
from synthetic import aggregate, random_walk
from test_strategy import BUY_SEED, SELL_SEED

from nexus_engine.backtest.baselines import buy_and_hold, random_entries
from nexus_engine.backtest.engine import Trade, run_backtest
from nexus_engine.backtest.metrics import compute_metrics, max_drawdown
from nexus_engine.backtest.portfolio import OpenPosition, Portfolio, RiskRules
from nexus_engine.signals.risk import CostModel
from nexus_engine.signals.series import CandleSeries

H = timedelta(hours=1)
T = datetime(2026, 1, 1, tzinfo=UTC)


def test_window_matches_production_lookback() -> None:
    candles = random_walk("1h", 600, 5)
    s = CandleSeries.from_candles("1h", candles)
    w = s.window(500, 310)
    ref = CandleSeries.from_candles("1h", candles[191:501])
    assert w.open_times == ref.open_times
    for a, b in ((w.open, ref.open), (w.high, ref.high), (w.low, ref.low), (w.close, ref.close), (w.volume, ref.volume)):
        np.testing.assert_array_equal(a, b)


@pytest.fixture(scope="module")
def results():
    out = []
    for seed in (BUY_SEED, SELL_SEED):
        candles = random_walk("1h", 1400, seed)
        start, end = candles[999].close_time, candles[-1].close_time
        out.append((candles, start, end, run_backtest("1h", candles, aggregate(candles, "1h", "4h"), start, end)))
    return out


def test_backtest_accounting_invariants(results) -> None:
    total = 0
    for _, start, end, r in results:
        total += len(r.trades)
        assert r.decisions > 0
        assert all(start <= t.decided_at <= end and t.closed_at > t.decided_at for t in r.trades)
        assert r.final_equity == RiskRules().initial_equity + sum((t.pnl for t in r.trades), D(0))
        times = [t for t, _ in r.equity_curve]
        assert times == sorted(times)
        for t in r.trades:
            if t.entry_price is None:
                assert t.pnl == 0 and t.quantity == 0
            elif t.outcome == "loss" and t.exit_price == t.stop_loss:
                # Fixed-fractional sizing: a stop-out loses about 1% of equity, plus costs.
                assert abs(t.pnl) <= D("0.0105") * r.final_equity * D("1.2")
        assert r.assumptions["lookback_bars"] == 310
    assert total > 0, "the strategy should trade at least once on these synthetic series"


def test_portfolio_rules() -> None:
    rules = RiskRules(initial_equity=D(10_000))
    pf = Portfolio(rules)
    assert pf.size(D(100), D(95)) == D(20)  # 1% of 10,000 / 5 = 20 units = 2,000 notional (under the 25% cap)
    assert pf.size(D(100), D("99.9")) == D(25)  # capped at 25% notional
    pf.add(OpenPosition("BUY", T + H, D(-100)))
    assert pf.can_open(T, "BUY") == "open_position_same_direction"
    pf.add(OpenPosition("SELL", T + 2 * H, D(-250)))
    pf.add(OpenPosition("SELL", T + 3 * H, D(0)))
    assert pf.can_open(T, "BUY") in ("open_position_same_direction", "max_concurrent")
    pf.realize_until(T + 3 * H)
    assert pf.equity == D(9_650) and pf.can_open(T + 4 * H, "BUY") == "daily_loss_limit"  # -350 >= 3% of 10,000
    assert pf.can_open(T + timedelta(days=1), "BUY") is None
    pf.add(OpenPosition("BUY", T + timedelta(days=2), D(-1_200)))
    pf.realize_until(T + timedelta(days=2))
    assert pf.halted_at is not None and pf.can_open(T + timedelta(days=3), "BUY") == "drawdown_halt"


def trade(seq: int, pnl: str, r: str, ret: str, outcome: str = "win") -> Trade:
    return Trade(seq, "BUY", T + seq * H, T + (seq + 1) * H, outcome, D(95), D(110), D(100), D(101), D(1), D(ret), D(r), D(pnl))


def test_metrics_hand_computed() -> None:
    trades = [trade(1, "200", "2", "0.02"), trade(2, "-100", "-1", "-0.01", "loss"), trade(3, "100", "1", "0.01"),
              Trade(4, "BUY", T, T + H, "expired_no_entry", D(95), D(110), None, None, D(0), None, None, D(0))]  # fmt: skip
    curve = [(T, D(10_000)), (T + 2 * H, D(10_200)), (T + 3 * H, D(10_100)), (T + 4 * H, D(10_200))]
    m = compute_metrics(trades, curve, D(10_000), T, T + timedelta(days=5), seed=1)
    assert (m["signals"], m["trades"]) == (4, 3)
    assert m["win_rate"] == "0.666667" and m["profit_factor"] == "3.000000"
    assert m["net_return"] == "0.020000" and m["expectancy_r"] == "0.6667"
    assert m["max_drawdown"] == f"{100 / 10_200:.6f}"
    assert m["insufficient_sample"] is True and m["outcomes"]["expired_no_entry"] == 1
    assert compute_metrics(trades, curve, D(10_000), T, T + timedelta(days=5), seed=1) == m  # deterministic


def test_metrics_without_losses_or_trades() -> None:
    m = compute_metrics([], [(T, D(10_000))], D(10_000), T, T + timedelta(days=1))
    assert m["trades"] == 0 and m["win_rate"] is None and m["profit_factor"] is None and m["sharpe"]["value"] is None
    assert max_drawdown([(T, D(1)), (T, D(2))]) == 0


def test_baselines() -> None:
    candles = random_walk("1h", 400, 3)
    start, end = candles[10].close_time, candles[-1].close_time
    zero = CostModel(D(0), D(0))
    bh = buy_and_hold(candles, start, end, zero)
    assert bh == str(((candles[-1].close / candles[10].close) - 1).quantize(D("1e-8")))
    trades = [trade(1, "10", "1", "0.01")]
    a = random_entries(candles, "1h", trades, start, end, zero, 6, 24, runs=20, seed=4)
    assert a == random_entries(candles, "1h", trades, start, end, zero, 6, 24, runs=20, seed=4)
    assert a is not None and 0 <= float(a["strategy_percentile"]) <= 1
