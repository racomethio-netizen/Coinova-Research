"""The strategy-module test harness on synthetic data: drawdown, metrics, gates, and one
forex module end to end from a CSV in the Exness bar format."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import modules_bt as mb
from nexus_strategies.modules import sim

sys.path.insert(0, str(Path(__file__).parent))
from test_strategy_modules import fx_m5


def trade(sym: str, day: int, r: float, carry: float = 0.0) -> sim.Trade:
    t = int(pd.Timestamp("2020-01-01", tz="UTC").value) + day * 86_400 * 10**9
    return sim.Trade(sym, 1, t, t, 1.0, 0.9, 1.2, 0.1, t + 3600 * 10**9, 1.0, "time", r, 3,
                     carry)


def test_drawdown_compounds_the_risk_per_trade():
    assert mb.equity_drawdown(np.array([1.0, -1.0, -1.0])) == pytest.approx(
        1 - (1 - 0.0025) ** 2)
    assert mb.equity_drawdown(np.array([])) == 0.0


def test_metrics_and_gates():
    start, end = pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2020-12-31", tz="UTC")
    trs = [trade("A", d, 2.0 if d % 3 == 0 else -1.0) for d in range(0, 360, 2)]
    m = mb.metrics(trs, start, end)
    assert m["trades"] == 180 and m["mean_r"] == pytest.approx(0.0)
    assert m["profit_factor"] == pytest.approx(1.0)
    assert set(m["half_mean_r"]) == {"first", "second"} and m["p_value"] is not None
    g = mb.gates("fx_scalp_pullback", m, m, significant=False)
    assert g["enough_trades"] is False and g["positive_expectancy"] is False
    good = [trade("A", d, 2.0 if d % 2 == 0 else -1.0) for d in range(0, 360)]
    mg = mb.metrics(good, start, end)
    g = mb.gates("fx_scalp_pullback", mg, mg, significant=True)
    assert all(g.values()), g
    assert mb.metrics([], start, end) == {"trades": 0}


def test_one_forex_module_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    m5 = fx_m5(days=200)
    m5 = m5[m5.index >= "2024-01-01"]
    csv = m5.rename(columns={"spread": "spread_mean"})
    csv.insert(0, "time", csv.index.strftime("%Y-%m-%dT%H:%M:%SZ"))
    csv["ticks"], csv["spread_median"] = 100, csv["spread_mean"]
    csv.to_csv(tmp_path / "EURUSD_M5.csv", index=False)
    monkeypatch.setattr(mb, "FX_START", pd.Timestamp("2024-02-01", tz="UTC"))
    monkeypatch.setattr(mb, "fx_carry", lambda fred, pair: None)
    res = mb.run_job(mb.Job("fx_scalp_pullback", "EURUSD", str(tmp_path), "", ""))
    assert res["signals"] > 0 and res["normal"] and len(res["double"]) == len(res["normal"])
    first = pd.Timestamp(res["normal"][0]["signal_time"], tz="UTC")
    assert first >= pd.Timestamp("2024-02-01", tz="UTC")
    ends = {"forex": pd.Timestamp(res["last_bar"]), "crypto": pd.Timestamp(res["last_bar"])}
    out = mb.evaluate([res], ends)
    fx = out["fx_scalp_pullback"]
    assert fx["metrics"]["trades"] <= len(res["normal"])
    assert fx["metrics_2x_costs"]["mean_r"] < fx["metrics"]["mean_r"]
    assert fx["gates"] is not None and fx["passed"] in (True, False)
    assert out["crypto_breakout_retest"]["gates"] is None             # earlier verdict stands
    assert "| Module |" in mb.render({"modules": {**out, "crypto_swing_trend": {
        "replication": {"passed": True, "gates": {}}}}})


def test_load_fx_drops_and_counts_bad_rows(tmp_path: Path):
    rows = ["time,open,high,low,close,ticks,spread_mean,spread_median",
            "2024-01-02T00:00:00Z,1.1,1.2,1.0,1.1,5,0.0001,0.0001",
            "2024-01-02T00:05:00Z,1.1,1.0,1.2,1.1,5,0.0001,0.0001",      # high < low
            "2024-01-02T00:10:00Z,1.1,1.2,1.0,1.1,5,-0.1,0.0001"]          # negative spread
    (tmp_path / "EURUSD_M5.csv").write_text("\n".join(rows) + "\n")
    df, info = mb.load_fx(tmp_path, "EURUSD")
    assert len(df) == 1 and info == {"rows": 3, "dropped": 2, "kept": 1}
