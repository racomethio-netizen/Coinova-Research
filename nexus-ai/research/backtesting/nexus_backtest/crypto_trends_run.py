"""Single pre-registered run (docs/research/2026-11-23-crypto-trends-preregistration.md).

``python -m nexus_backtest.crypto_trends_run --archive DIR --out FILE``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from nexus_backtest.crypto_trends import (
    LOOKBACKS,
    SimConfig,
    buy_and_hold,
    cagr,
    calendar_years,
    load_archive_dir,
    max_drawdown,
    sharpe,
    simulate,
)

PRIMARY = (pd.Timestamp("2022-11-14", tz="UTC"), pd.Timestamp("2024-11-14", tz="UTC"))


def summary(eq: pd.Series) -> dict[str, Any]:
    years = calendar_years(eq)
    return {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "final_multiple": float(eq.iloc[-1] / eq.iloc[0]), "cagr": cagr(eq),
            "sharpe": sharpe(eq), "max_drawdown": max_drawdown(eq), "years": years}


def evaluate(bars: dict[str, pd.DataFrame], cfg: SimConfig) -> dict[str, Any]:
    idx = sorted(set.intersection(*(set(b.index) for b in bars.values())))
    start = pd.DatetimeIndex(idx)[max(LOOKBACKS)]          # 360 days of history for both
    res = simulate(bars, cfg, start=start)
    eq = res.equity
    mid = eq.index[0] + (eq.index[-1] - eq.index[0]) / 2
    h1, h2 = eq[eq.index < mid], eq[eq.index >= mid]
    bh = buy_and_hold(bars, eq.index)
    prim = eq[(eq.index >= PRIMARY[0]) & (eq.index < PRIMARY[1])]
    years = calendar_years(eq)
    out = {"strategy": summary(eq), "half_1": summary(h1), "half_2": summary(h2),
           "buy_and_hold": summary(bh),
           "primary_unseen_period": {"return": float(prim.iloc[-1] / prim.iloc[0] - 1),
                                     "sharpe": sharpe(prim), "max_drawdown": max_drawdown(prim),
                                     "buy_and_hold_return": float(
                                         bh[prim.index].iloc[-1] / bh[prim.index].iloc[0] - 1)},
           "trades": res.trades, "turnover_x_per_year": res.turnover / (len(eq) / 365),
           "costs_paid": res.costs,
           "avg_exposure": {s: float(res.weights[s].mean()) for s in res.weights}}
    s = out["strategy"]
    gates = {
        "sharpe_ge_0.8": (s["sharpe"] or 0) >= 0.8,
        "max_drawdown_le_35pct": s["max_drawdown"] <= 0.35,
        "both_halves_sharpe_ge_0.5": (out["half_1"]["sharpe"] or 0) >= 0.5
        and (out["half_2"]["sharpe"] or 0) >= 0.5,
        "positive_years_ge_60pct": bool(years) and
        sum(v > 0 for v in years.values()) / len(years) >= 0.6,
        "sharpe_above_buy_and_hold": (s["sharpe"] or 0) > (out["buy_and_hold"]["sharpe"] or 0),
        "primary_period_positive": out["primary_unseen_period"]["return"] > 0,
    }
    out["gates"] = gates
    out["passed"] = all(gates.values())
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--archive", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    bars = {sym: load_archive_dir(Path(a.archive), sym) for sym in ("BTCUSDT", "ETHUSDT")}
    report: dict[str, Any] = {"decision": evaluate(bars, SimConfig())}
    # Reported only (do not decide).
    report["per_coin"] = {s: evaluate({s: bars[s]}, SimConfig())["strategy"] for s in bars}
    report["paper_cap_200pct"] = evaluate(bars, SimConfig(cap=2.0))["strategy"]
    report["zero_costs"] = evaluate(bars, SimConfig(fee=0, slippage=0))["strategy"]
    report["double_costs"] = evaluate(bars, SimConfig(fee=0.002, slippage=0.001))["strategy"]
    report["single_lookbacks"] = {n: evaluate(bars, SimConfig(lookbacks=(n,)))["strategy"]
                                  for n in LOOKBACKS}
    report["data"] = {s: [str(b.index[0].date()), str(b.index[-1].date()), len(b)]
                      for s, b in bars.items()}
    Path(a.out).write_text(json.dumps(report, indent=1, default=str))
    d = report["decision"]
    print("PASS" if d["passed"] else "FAIL", json.dumps(d["gates"]))
    print("strategy", json.dumps(d["strategy"], default=str))
    print("buy&hold", json.dumps(d["buy_and_hold"], default=str))


if __name__ == "__main__":
    main()
