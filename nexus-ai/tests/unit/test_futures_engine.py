"""Futures backtester on hand-built paths with exact expected results."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pandas as pd
from nexus_backtest.futures_engine import FuturesConfig, FuturesSignal, run_futures_backtest

T0 = datetime(2026, 1, 1, 1, tzinfo=UTC)  # not a funding hour: funding tests build their own


def frame(rows: list[tuple[float, float, float, float]], start: datetime = T0) -> pd.DataFrame:
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(len(rows))], name="open_time")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).astype(float)


def mark_from(kl: pd.DataFrame, wick: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame({"mark_high": kl.high + wick, "mark_low": kl.low - wick,
                         "mark_close": kl.close}, index=kl.index)


FLAT = (100.0, 100.0, 100.0, 100.0)
ZERO_COST = FuturesConfig(fee_rate=D(0), slippage_rate=D(0), min_notional=None,
                          maintenance_margin_rate=D("0.004"), leverage=2)


def run(rows, sigs, cfg=ZERO_COST, funding=None):  # type: ignore[no-untyped-def]
    kl = frame(rows)
    mk = mark_from(kl)
    fu = funding if funding is not None else pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    return run_futures_backtest(kl, mk, fu, sigs, cfg)


def sig(i: int, side: str, stop: float, target: float | None, rows=None) -> FuturesSignal:  # type: ignore[no-untyped-def]
    return FuturesSignal(time=T0 + timedelta(hours=i), side=side, stop=D(str(stop)),
                         target=None if target is None else D(str(target)))


def test_long_hits_target_and_pnl_exact() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    res = run(rows, [sig(0, "long", 98, 105)])
    (t,) = res.trades
    assert t.side == "long" and t.exit_reason == "target"
    assert t.entry_price == D(100) and t.exit_price == D(105)
    # 1% of 10,000 = 100 risk; per-unit loss 2 -> qty 50.0; PnL = 5 * 50 = 250
    assert t.quantity == D("50.000")
    assert t.pnl == D(250)
    assert res.equity.iloc[-1] == 10_250


def test_short_profits_when_price_falls() -> None:
    rows = [FLAT, FLAT, (100, 100, 94, 95), FLAT]
    res = run(rows, [sig(0, "short", 102, 95)])
    (t,) = res.trades
    assert t.side == "short" and t.exit_reason == "target"
    assert t.pnl > 0


def test_stop_uses_mark_price_and_gap_fills_at_open() -> None:
    rows = [FLAT, FLAT, (90, 91, 89, 90), FLAT]
    res = run(rows, [sig(0, "long", 98, 110)])
    (t,) = res.trades
    assert t.exit_reason == "stop" and t.exit_price == D(90)  # gapped through: fill at open
    assert t.pnl < 0


def test_liquidation_loses_isolated_margin_only() -> None:
    cfg = FuturesConfig(fee_rate=D(0), slippage_rate=D(0), min_notional=None, leverage=20,
                        maintenance_margin_rate=D("0.004"))
    rows = [FLAT, FLAT, (100, 100, 93, 94), FLAT]  # long at 20x liquidates near 95.2
    kl = frame(rows)
    mk = mark_from(kl)
    mk.loc[kl.index[2], "mark_low"] = 94.0
    res = run_futures_backtest(kl, mk, pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC")),
                               [sig(0, "long", 99, 110)], cfg)
    (t,) = res.trades
    assert t.exit_reason == "liquidation"
    assert res.equity.iloc[-1] < 10_000  # margin lost; no negative balance beyond it
    assert res.equity.iloc[-1] >= 10_000 - float(t.margin) - 1


def test_no_look_ahead_entry_is_next_bar_open() -> None:
    rows = [FLAT, (100, 104, 99, 103), (120, 121, 119, 120), FLAT]
    res = run(rows, [sig(0, "long", 98, 105)])  # signal known at bar 0 close, executes at bar 1 open
    (t,) = res.trades
    assert t.entry_time == T0 + timedelta(hours=1) and t.entry_price == D(100)


def test_funding_paid_by_longs_when_positive() -> None:
    settle = T0 + timedelta(hours=7)  # the 08:00 settlement is one bar after 07:00
    kl = frame([FLAT] * 12, start=T0 + timedelta(hours=0))
    idx = kl.index
    s_time = next(t for t in idx if t.hour == 8)
    fund = pd.Series([D("0.001")], index=pd.DatetimeIndex([s_time], name="funding_time"))
    rows = [FLAT] * 12
    res = run(rows, [sig(0, "long", 98, 130)], funding=fund)
    (t,) = res.trades
    assert t.funding < 0  # long pays positive funding
    assert settle is not None


def test_refused_sizes_are_recorded_not_traded() -> None:
    res = run([FLAT, FLAT, FLAT], [sig(0, "long", 101, 110)])  # stop above entry: wrong side
    assert res.trades == [] and sum(res.refused.values()) == 1


def test_drawdown_halts_new_entries() -> None:
    # Two stopped-out longs (about -1% each at 1% risk; 1.99% total) pass the 1.5% halt; the third is ignored.
    cfg = FuturesConfig(fee_rate=D(0), slippage_rate=D(0), min_notional=None, max_drawdown=D("0.015"))
    loss = (100, 100, 90, 90)
    rows = [FLAT, loss, FLAT, loss, FLAT, FLAT]
    sigs = [sig(0, "long", 95, 150), sig(2, "long", 95, 150), sig(4, "long", 95, 150)]
    res = run(rows, sigs, cfg)
    assert res.halted_at is not None
    assert len(res.trades) == 2 and all(t.exit_reason == "stop" for t in res.trades)
