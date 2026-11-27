"""Volatility control on Exness gold (XAUUSD) and NASDAQ-100 (USTEC) CFDs.

Pre-registration: docs/research/2026-11-27-exness-voltarget-preregistration.md.

The rule is the NASDAQ-100 test's, unchanged:
* weight = min(1, 15% / vol), with vol from the last 30 daily returns (annualised, sqrt 252);
* trade only when more than 10 points off target;
* never above 1x equity.

Fills use Exness's own bid/ask at the last M5 bar of each UTC weekday. Two financing
scenarios are run:
* A, swap-free;
* B, long financing at T-bill + 2.5% a year.

Usage: ``python -m nexus_backtest.exness_voltarget --gold DIR --ustec DIR --fred DIR
--out FILE``
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.multi_asset_trend import read_fred

TARGET, WINDOW, ANN, BAND = 0.15, 30, 252, 0.10
SLIP = 0.0001
MARKUP = 0.025
WARMUP_DAYS = 60
PREREG = "docs/research/2026-11-27-exness-voltarget-preregistration.md"


def daily(m5_csv: Path) -> pd.DataFrame:
    """Bid and ask close at the last M5 bar of each UTC weekday."""
    m5 = pd.read_csv(m5_csv, index_col=0, usecols=["time", "close", "ask_close"])
    m5.index = pd.to_datetime(m5.index, utc=True)
    m5 = m5[m5.index.day_of_week < 5]
    d = m5.groupby(m5.index.normalize()).last()
    d.columns = ["bid", "ask"]
    return d[(d["bid"] > 0) & (d["ask"] >= d["bid"])]


def simulate(d: pd.DataFrame, start: pd.Timestamp, *, rule: bool = True,
             cost_mult: float = 1.0, rate: pd.Series | None = None,
             capital: float = 1.0) -> pd.DataFrame:
    """Equity per decision day from ``start``. ``rule=False`` = hold at 100% from day one.
    ``rate`` (annual, decimal) switches on long financing at rate + MARKUP."""
    bid, ask = d["bid"].to_numpy(float), d["ask"].to_numpy(float)
    r = d["bid"].pct_change()
    vol = (r.rolling(WINDOW, min_periods=WINDOW).std() * math.sqrt(ANN)).to_numpy(float)
    with np.errstate(divide="ignore"):                    # zero volatility -> weight 1
        want = np.minimum(1.0, TARGET / vol)
    cash, units, prev_t = capital, 0.0, None
    rows = []
    for i, t in enumerate(d.index):
        if t < start:
            continue
        spread = ask[i] - bid[i]
        if rate is not None and units > 0 and prev_t is not None:
            days = (t - prev_t).days
            ann = float(rate.asof(t)) + MARKUP
            cash -= units * bid[i] * ann * days / 365
        prev_t = t
        eq = cash + units * bid[i]
        w = 1.0 if not rule else want[i]
        held = units * bid[i] / eq if eq > 0 else 0.0
        if not math.isnan(w) and (abs(w - held) > BAND or (not rule and units == 0)):
            value = w * eq - units * bid[i]                # notional to buy (+) or sell (-)
            if value > 0:
                px = (ask[i] + (cost_mult - 1) * spread) * (1 + SLIP * cost_mult)
                units += value / px
                cash -= value
            else:
                px = (bid[i] - (cost_mult - 1) * spread) * (1 - SLIP * cost_mult)
                q = -value / bid[i]
                units -= q
                cash += q * px
            eq = cash + units * bid[i]
        rows.append({"date": t, "equity": eq, "units": units,
                     "weight": units * bid[i] / eq if eq > 0 else 0})
    return pd.DataFrame(rows).set_index("date")


def stats(eq: pd.Series) -> dict[str, Any]:
    r = eq.pct_change().dropna()
    sd = float(r.std())
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    growth = float(eq.iloc[-1] / eq.iloc[0])
    return {"net_return": growth - 1, "annual_return": growth ** (1 / years) - 1,
            "sharpe": float(r.mean() / sd * math.sqrt(ANN)) if sd > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "by_year": {str(y): float(g.iloc[-1] / g.iloc[0] - 1) if len(g) > 1 else 0.0
                        for y, g in eq.groupby(eq.index.year)}}


def evaluate(series: dict[str, pd.DataFrame], start: pd.Timestamp,
             rate: pd.Series) -> dict[str, Any]:
    """One portfolio: equal capital in each instrument's sleeve, each running the rule."""
    def run(**kw: Any) -> pd.Series:
        parts = [simulate(d, start, capital=1 / len(series), **kw)["equity"]
                 for d in series.values()]
        return pd.concat(parts, axis=1, sort=True).ffill().dropna().sum(axis=1)

    strat, hold = run(), run(rule=False)
    strat2 = run(cost_mult=2.0)
    strat_b, hold_b = run(rate=rate), run(rule=False, rate=rate)
    mid = strat.index[0] + (strat.index[-1] - strat.index[0]) / 2
    halves = {h: {"strategy": stats(s), "hold": stats(hh)} for h, s, hh in (
        ("first", strat[strat.index < mid], hold[hold.index < mid]),
        ("second", strat[strat.index >= mid], hold[hold.index >= mid]))}
    m, hm, m2, mb = stats(strat), stats(hold), stats(strat2), stats(strat_b)
    gates = {
        "sharpe_above_hold": (m["sharpe"] or 0) > (hm["sharpe"] or 0),
        "drawdown_10pts_smaller": m["max_drawdown"] >= hm["max_drawdown"] + 0.10,
        "net_positive": m["net_return"] > 0,
        "sharpe_above_hold_both_halves": all(
            (v["strategy"]["sharpe"] or 0) > (v["hold"]["sharpe"] or 0)
            for v in halves.values()),
        "holds_at_2x_costs": (m2["sharpe"] or 0) > (hm["sharpe"] or 0) and
            m2["net_return"] > 0,
        "net_positive_with_swaps": mb["net_return"] > 0}
    weights = pd.concat([simulate(d, start)["weight"] for d in series.values()], axis=1, sort=True)
    share = min(1.0, 0.05 / abs(m["max_drawdown"])) if m["max_drawdown"] < 0 else 1.0
    return {"start": str(strat.index[0].date()), "end": str(strat.index[-1].date()),
            "strategy": m, "hold": hm, "strategy_2x_costs": m2,
            "with_swaps": {"strategy": mb, "hold": stats(hold_b)}, "halves": halves,
            "avg_weight": float(weights.mean(axis=1).mean()), "gates": gates,
            "passed": all(gates.values()),
            "nexus_fit": {"allocation_share_for_5pct_drawdown": share,
                          "annual_return_at_that_share": share * m["annual_return"]}}


def run(gold_dir: Path, ustec_dir: Path, fred: Path) -> dict[str, Any]:
    gold, ustec = daily(gold_dir / "XAUUSD_M5.csv"), daily(ustec_dir / "USTEC_M5.csv")
    rate = (read_fred(fred, "DTB3") / 100)
    rate.index = pd.to_datetime(rate.index).tz_localize("UTC")
    rate = rate.sort_index()
    g0 = gold.index[0] + pd.Timedelta(days=WARMUP_DAYS)
    u0 = ustec.index[0] + pd.Timedelta(days=WARMUP_DAYS)
    return {"portfolios": {
        "gold": evaluate({"XAUUSD": gold}, g0, rate),
        "nasdaq100": evaluate({"USTEC": ustec}, u0, rate),
        "gold_nasdaq_50_50": evaluate({"XAUUSD": gold, "USTEC": ustec}, u0, rate)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--ustec", type=Path, required=True)
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    rep = run(a.gold, a.ustec, a.fred)
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               preregistration=PREREG, rule={"target": TARGET, "window": WINDOW,
                                             "annualisation": ANN, "band": BAND,
                                             "max_leverage": 1.0})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: {"passed": v["passed"], "failed": [g for g, ok in v["gates"].items()
                                                            if not ok],
                          "annual": v["strategy"]["annual_return"],
                          "hold_annual": v["hold"]["annual_return"],
                          "dd": v["strategy"]["max_drawdown"],
                          "hold_dd": v["hold"]["max_drawdown"]}
                      for k, v in rep["portfolios"].items()}, indent=1))


if __name__ == "__main__":
    main()
