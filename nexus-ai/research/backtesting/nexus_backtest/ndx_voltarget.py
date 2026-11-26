"""Volatility-managed NASDAQ-100 holding (multi-asset Stage 3).

Pre-registration: docs/research/2026-11-26-ndx-voltarget-preregistration.md. The same rule
as the BTC/ETH test (``batch3.vol_target``) with stock-market annualisation (√252), an
optional cash rate, and a 0.03% per-side cost.

Usage: ``python -m nexus_backtest.ndx_voltarget --fred D --out F``
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from nexus_backtest.multi_asset_trend import provenance, read_fred

START, END = pd.Timestamp("1990-01-01"), pd.Timestamp("2026-11-01")
HALF = pd.Timestamp("2008-01-01")
COST, TARGET, BAND, WINDOW, ANN = 0.0003, 0.15, 0.10, 30, 252


def vol_target(px: pd.Series, target: float, *, cost: float = COST,
               cash_rate: pd.Series | None = None) -> pd.Series:
    """Daily net returns of an index/cash portfolio. At each close the target weight is
    min(1, target / 30-day realised vol (√252)) from closes up to that close; it trades only
    when the drifting held weight is more than 10 points away. Cash earns ``cash_rate``
    (annual, decimal, applied from the previous day) or nothing."""
    r = px.pct_change()
    vol = r.rolling(WINDOW, min_periods=WINDOW).std() * math.sqrt(ANN)
    want = (target / vol).clip(upper=1.0)
    days = px.index.to_series().diff().dt.days.fillna(0).to_numpy()
    rate = cash_rate.reindex(px.index, method="ffill").shift(1).fillna(0.0).to_numpy() \
        if cash_rate is not None else None
    cash, units, prev = 1.0, 0.0, 1.0
    out = []
    for i, (p, w) in enumerate(zip(px.to_numpy(float), want.to_numpy(float), strict=True)):
        if rate is not None:
            cash *= 1 + rate[i] * days[i] / 365
        eq = cash + units * p
        if not math.isnan(w) and abs(w - units * p / eq) > BAND:
            trade = w * eq - units * p
            fee = abs(trade) * cost
            units += trade / p
            cash -= trade + fee
            eq -= fee
        out.append(eq / prev - 1)
        prev = eq
    return pd.Series(out, index=px.index)


def stats(r: pd.Series) -> dict[str, Any]:
    r = r.dropna()
    eq = (1 + r).cumprod()
    sd = float(r.std(ddof=1))
    years = (r.index[-1] - r.index[0]).days / 365.25
    return {"net_return": float(eq.iloc[-1] - 1),
            "annual_return": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
            "sharpe": float(r.mean() / sd * math.sqrt(ANN)) if sd > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min())}


def report(px: pd.Series, cash_rate: pd.Series) -> dict[str, Any]:
    win = (px.index >= START) & (px.index < END)
    hold_r = px.pct_change()
    hold = stats(hold_r[win])
    main = stats(vol_target(px, TARGET)[win])
    main2 = stats(vol_target(px, TARGET, cost=2 * COST)[win])
    halves = {}
    for name, a, b in (("1990_2007", START, HALF), ("2008_2026", HALF, END)):
        sel = (px.index >= a) & (px.index < b)
        halves[name] = {"strategy": stats(vol_target(px, TARGET)[sel]),
                        "hold": stats(hold_r[sel])}
    gates = {"sharpe_above_hold": (main["sharpe"] or 0) > (hold["sharpe"] or 0),
             "drawdown_10pts_smaller": main["max_drawdown"] >= hold["max_drawdown"] + 0.10,
             "net_positive": main["net_return"] > 0,
             "sharpe_above_hold_both_halves": all(
                 (v["strategy"]["sharpe"] or 0) > (v["hold"]["sharpe"] or 0)
                 for v in halves.values()),
             "holds_at_2x_costs": (main2["sharpe"] or 0) > (hold["sharpe"] or 0)
             and main2["net_return"] > 0}
    want = (TARGET / (px.pct_change().rolling(WINDOW, min_periods=WINDOW).std()
                      * math.sqrt(ANN))).clip(upper=1.0)
    return {"hold": hold, "target_15": main, "target_15_2x_costs": main2, "halves": halves,
            "target_10": stats(vol_target(px, 0.10)[win]),
            "target_20": stats(vol_target(px, 0.20)[win]),
            "target_15_cash_earns_tbill": stats(vol_target(px, TARGET,
                                                           cash_rate=cash_rate)[win]),
            "avg_weight_target_15": float(want[win].mean()),
            "gates": gates, "passed": all(gates.values())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    px = read_fred(a.fred, "NASDAQ100")
    px = px[px.index < END]
    rep = report(px, read_fred(a.fred, "DTB3") / 100)
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               window=[str(START.date()), "2026-10-31"],
               preregistration="docs/research/2026-11-26-ndx-voltarget-preregistration.md",
               data={"source": "FRED, Federal Reserve Bank of St. Louis",
                     "series": provenance(a.fred, ["NASDAQ100", "DTB3"])})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"passed": rep["passed"], "gates": rep["gates"]}))


if __name__ == "__main__":
    main()
