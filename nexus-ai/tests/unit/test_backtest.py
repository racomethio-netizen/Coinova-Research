"""Backtester mechanics on hand-built price paths (exact expected fills)."""

from __future__ import annotations

from decimal import Decimal as D
from typing import ClassVar

import pandas as pd
import pytest
from nexus_backtest.engine import BacktestConfig, MarketSpec, run_backtest
from nexus_backtest.metrics import compute_metrics, max_drawdown
from nexus_backtest.signals import CostModel, study_signals
from nexus_strategies.base import Strategy

T0 = pd.Timestamp("2026-01-01", tz="UTC")


def bars(rows: list[tuple[float, float, float, float]], start: pd.Timestamp = T0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC", name="open_time")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


class Scripted(Strategy):
    """Emits exactly the signals given; base timeframe 5m for precise tests."""

    code = "scripted"
    base_tf, confirm_tf = "5m", "5m"
    defaults: ClassVar[dict[str, float]] = {}
    script: ClassVar[dict[pd.Timestamp, tuple[str, float, float]]] = {}

    def rules(self, df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - not used
        raise NotImplementedError

    def compute(self, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        idx = frames["5m"].index
        out = pd.DataFrame({"entry": False, "exit": False, "stop": float("nan"),
                            "target": float("nan"), "confidence": 0.5, "regime": "range"},
                           index=idx)
        for t, (kind, stop, target) in self.script.items():
            if kind == "entry":
                out.loc[t, ["entry", "stop", "target"]] = [True, stop, target]
            else:
                out.loc[t, "exit"] = True
        return out


def run(rows: list[tuple[float, float, float, float]], script: dict[int, tuple[str, float, float]],
        *, fee: str = "0", slip: str = "0", symbols: tuple[str, ...] = ("BTC/USDT",),
        cfg: BacktestConfig | None = None):  # type: ignore[no-untyped-def]
    df = bars(rows)
    Scripted.script = {df.index[i]: v for i, v in script.items()}
    data = {s: {"5m": df} for s in symbols}
    specs = {s: MarketSpec(s, s.split("/")[0] if "BTC" in s else "ETH", D("0.0001"))
             for s in symbols}
    cfg = cfg or BacktestConfig(fee_rate=D(fee), slippage_rate=D(slip))
    return run_backtest(data, [Scripted()], specs, cfg)


FLAT = (100.0, 100.0, 100.0, 100.0)


def test_entry_at_next_open_and_target_fill() -> None:
    rows = [FLAT, (101, 101, 101, 101), (101, 106, 100.5, 105), FLAT]
    res = run(rows, {0: ("entry", 95.0, 105.0)})
    (t,) = res.trades
    assert t.entry_time == bars(rows).index[1] and t.entry_price == D("101.0")
    assert t.exit_reason == "target" and t.exit_price == D("105.0")
    # risk sizing: 25 / (101-95) = 4.1666 -> concentration cap 2500/101 = 24.75 ... risk binds
    assert t.quantity == D("4.1666")


def test_stop_wins_when_both_touched_same_bar() -> None:
    rows = [FLAT, FLAT, (100, 110, 90, 100), FLAT]
    (t,) = run(rows, {0: ("entry", 95.0, 105.0)}).trades
    assert t.exit_reason == "stop" and t.exit_price == D("95.0")


def test_gap_through_stop_fills_at_open() -> None:
    rows = [FLAT, FLAT, (90, 91, 89, 90), FLAT]
    (t,) = run(rows, {0: ("entry", 95.0, 105.0)}).trades
    assert t.exit_reason == "stop_gap" and t.exit_price == D("90.0")


def test_gap_at_entry_skips_trade() -> None:
    res = run([FLAT, (94, 94, 94, 94), FLAT], {0: ("entry", 95.0, 105.0)})
    assert res.trades == [] and res.rejected_signals == {"gapped beyond levels": 1}


def test_costs_applied_both_legs() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    (t,) = run(rows, {0: ("entry", 95.0, 105.0)}, fee="0.001", slip="0.0005").trades
    assert t.entry_price == D("100.05")
    assert t.exit_price == D("105.0") * D("0.9995")
    expected = (t.exit_price - t.entry_price) * t.quantity - t.entry_price * t.quantity * D(
        "0.001") - t.exit_price * t.quantity * D("0.001")
    assert t.pnl == expected


def test_strategy_exit_at_next_open() -> None:
    rows = [FLAT, FLAT, (100, 101, 99, 100), (102, 102, 102, 102), FLAT]
    (t,) = run(rows, {0: ("entry", 90.0, 120.0), 2: ("exit", 0, 0)}).trades
    assert t.exit_reason == "strategy_exit" and t.exit_price == D("102.0")


def test_no_duplicate_asset_and_max_positions() -> None:
    rows = [FLAT] * 6
    res = run(rows, {0: ("entry", 90.0, 120.0), 1: ("entry", 90.0, 120.0)})
    assert len(res.trades) == 1 and res.rejected_signals.get("duplicate asset") == 1


def test_drawdown_shutdown_stops_trading() -> None:
    # Huge stop distance + tight risk: force a >5% loss via gap, then no further entries.
    rows = [FLAT, FLAT, (40, 40, 40, 40), (40, 40, 40, 40), (40, 40, 40, 40), (40, 40, 40, 40)]
    cfg = BacktestConfig(fee_rate=D(0), slippage_rate=D(0))
    res = run(rows, {0: ("entry", 99.0, 150.0), 3: ("entry", 30.0, 60.0)}, cfg=cfg)
    kinds = [e[1] for e in res.events]
    assert "max_drawdown_shutdown" in kinds
    assert res.rejected_signals.get("drawdown shutdown") == 1


def test_metrics_shapes() -> None:
    rows = [FLAT, FLAT, (100, 106, 100, 105), FLAT]
    m = compute_metrics(run(rows, {0: ("entry", 95.0, 105.0)}))
    assert m["trades"] == 1 and m["win_rate"] == 1.0 and m["net_return"] > 0
    assert max_drawdown(pd.Series([100.0, 120, 90, 130])) == pytest.approx(0.25)


def test_signal_study_matches_engine_rules() -> None:
    rows = [FLAT, FLAT, (100, 110, 90, 100), FLAT, FLAT]
    df = bars(rows)
    Scripted.script = {df.index[0]: ("entry", 95.0, 105.0)}
    st = study_signals({"5m": df}, Scripted(), costs=CostModel(0, 0))
    assert st.outcome.tolist() == ["stop"] and st.ret.iloc[0] == pytest.approx(-0.05)
