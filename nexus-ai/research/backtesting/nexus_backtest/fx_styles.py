"""Institutional FX styles on the 9 G10 currencies against USD: carry, dollar carry,
momentum, value (5-year reversal) and their combination.

Pre-registration: docs/research/2026-11-26-fx-styles-preregistration.md. FRED daily spot
(H.10) and 3-month rates. Excess returns include carry. Monthly rebalancing on the first
trading day of each month from signals up to the previous trading day. 1x gross; costs
per currency.

Usage: ``python -m nexus_backtest.fx_styles --fred D --out F``
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.multi_asset_trend import FX, provenance, read_fred, stats
from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

G10: dict[str, tuple[str, bool, str]] = {
    **FX, "SEK": ("DEXSDUS", True, "IR3TIB01SEM156N"), "NOK": ("DEXNOUS", True,
                                                               "IR3TIB01NOM156N")}
USD_RATE = "DTB3"
COST = {**dict.fromkeys(("EUR", "JPY", "GBP", "CHF", "CAD", "AUD"), 0.0002),
        **dict.fromkeys(("NZD", "SEK", "NOK"), 0.0004)}
WARMUP = pd.Timestamp("1999-01-01")
START, END, HALF = (pd.Timestamp("2004-01-01"), pd.Timestamp("2026-11-01"),
                    pd.Timestamp("2016-01-01"))
N_SIDE, MOM_DAYS, VALUE_DAYS = 3, 63, 1260
STRATEGIES = ("carry", "dollar_carry", "momentum", "value", "combined")


# ------------------------------------------------------------------------------ data
def build(d: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """USD per unit of each currency, daily excess returns (spot + carry), and the
    interest differential (foreign - USD, decimal) in force on each day."""
    px = pd.DataFrame({c: (1 / read_fred(d, sid) if inv else read_fred(d, sid))
                       for c, (sid, inv, _) in G10.items()})
    px = px[(px.index >= WARMUP) & (px.index < END)].sort_index().ffill()
    usd = read_fred(d, USD_RATE).reindex(px.index, method="ffill") / 100
    days = px.index.to_series().diff().dt.days.fillna(1).to_numpy()
    diff = pd.DataFrame({c: read_fred(d, rid).reindex(px.index, method="ffill") / 100 - usd
                         for c, (_, _, rid) in G10.items()})
    ex = px.pct_change() + diff.shift(1).mul(days, axis=0) / 365
    return px, ex.iloc[1:], diff


# --------------------------------------------------------------------------- signals
def _rank_weights(score: pd.Series) -> pd.Series:
    s = score.dropna()
    w = pd.Series(0.0, index=score.index)
    if len(s) < 2 * N_SIDE:
        return w
    order = s.sort_values(kind="stable")
    w[order.index[-N_SIDE:]] = 1 / (2 * N_SIDE)
    w[order.index[:N_SIDE]] = -1 / (2 * N_SIDE)
    return w


def month_starts(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = index.to_series()
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).min().to_numpy())


def targets(px: pd.DataFrame, ex: pd.DataFrame, diff: pd.DataFrame
            ) -> dict[str, pd.DataFrame]:
    """Target weights per strategy on each month's first trading day, from data up to
    the previous trading day (every score is shifted by one row)."""
    carry = diff.reindex(ex.index).shift(1)
    mom = ((1 + ex).rolling(MOM_DAYS, min_periods=MOM_DAYS).apply(np.prod, raw=True) - 1
           ).shift(1)
    value = (-np.log(px / px.shift(VALUE_DAYS))).reindex(ex.index).shift(1)
    out: dict[str, dict[pd.Timestamp, pd.Series]] = {k: {} for k in STRATEGIES}
    for t in month_starts(ex.index):
        wc, wm, wv = (_rank_weights(x.loc[t]) for x in (carry, mom, value))
        out["carry"][t], out["momentum"][t], out["value"][t] = wc, wm, wv
        avg = carry.loc[t].mean()
        out["dollar_carry"][t] = pd.Series(
            (np.sign(avg) if avg == avg else 0.0) / len(G10), index=ex.columns)
        comb = (wc + wm + wv) / 3
        gross = comb.abs().sum()
        out["combined"][t] = comb / gross if gross > 0 else comb
    return {k: pd.DataFrame(v).T.reindex(columns=ex.columns) for k, v in out.items()}


# -------------------------------------------------------------------------- simulate
def simulate(tg: pd.DataFrame, ex: pd.DataFrame, *, cost_mult: float = 1.0,
             start: pd.Timestamp = START, end: pd.Timestamp = END) -> dict[str, Any]:
    r = ex[(ex.index >= start) & (ex.index < end)].fillna(0.0)
    cost = np.array([COST[c] for c in r.columns]) * cost_mult
    tmap = {t: row.fillna(0.0).to_numpy() for t, row in tg.iterrows()}
    w = np.zeros(r.shape[1])
    out, contrib, traded = [], np.zeros(r.shape[1]), 0.0
    rv = r.to_numpy()
    for i, t in enumerate(r.index):
        c = 0.0
        if t in tmap:
            delta = np.abs(tmap[t] - w)
            c = float((delta * cost).sum())
            traded += float(delta.sum())
            w = tmap[t].copy()
        pnl = w * rv[i]
        day = float(pnl.sum()) - c
        contrib += pnl
        out.append(day)
        w = w * (1 + rv[i]) / (1 + day) if 1 + day > 0 else np.zeros_like(w)
    daily = pd.Series(out, index=r.index)
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-9) if len(r) else 1.0
    return {"daily": daily, "turnover_per_year": traded / years,
            "contrib": pd.Series(contrib, index=r.columns)}


def evaluate(tg: pd.DataFrame, ex: pd.DataFrame) -> dict[str, Any]:
    base = simulate(tg, ex)
    stress = simulate(tg, ex, cost_mult=2.0)
    d = base["daily"]
    st = stats(d)
    contrib = base["contrib"].sort_values(ascending=False)
    monthly = (1 + d).groupby([d.index.year, d.index.month]).prod() - 1
    return {**st, "halves": {"2004_2015": stats(d[d.index < HALF]),
                             "2016_2026": stats(d[d.index >= HALF])},
            "stress_2x": stats(stress["daily"]), "turnover_per_year": base["turnover_per_year"],
            "contributions": {k: float(v) for k, v in contrib.items()},
            "best_currency": str(contrib.index[0]),
            "sum_without_best": float(d.sum() - contrib.iloc[0]),
            "worst_month": float(monthly.min()), "_daily": d}


def report(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pvals = {k: stationary_bootstrap_pvalue(v["_daily"].to_numpy(), simulations=5000,
                                            block=10) for k, v in results.items()}
    sig = holm(pvals)
    out: dict[str, Any] = {}
    for k, v in results.items():
        yrs = list(v["by_year"].values())
        g = {"net_positive": v["net_return"] > 0,
             "sharpe_ge_0_5": (v["sharpe"] or 0) >= 0.5,
             "significant_holm": bool(sig[k]),
             "drawdown_le_25pct": v["max_drawdown"] >= -0.25,
             "positive_both_halves": all(h["net_return"] > 0 for h in v["halves"].values()),
             "positive_55pct_of_years": sum(1 for x in yrs if x > 0) >= 0.55 * len(yrs),
             "positive_at_2x_costs": v["stress_2x"]["net_return"] > 0,
             "positive_without_best": v["sum_without_best"] > 0}
        out[k] = {**{x: y for x, y in v.items() if not x.startswith("_")}, "p_value": pvals[k],
                  "gates": g, "passed": all(g.values())}
    daily = pd.DataFrame({k: v["_daily"] for k, v in results.items()})
    return {"strategies": out,
            "correlations": {a: {b: float(daily[a].corr(daily[b])) for b in daily}
                             for a in daily},
            "summary": {"passed": [k for k, v in out.items() if v["passed"]]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    px, ex, diff = build(a.fred)
    tg = targets(px, ex, diff)
    rep = report({k: evaluate(tg[k], ex) for k in STRATEGIES})
    series = [v[0] for v in G10.values()] + [v[2] for v in G10.values()] + [USD_RATE]
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               window=[str(START.date()), "2026-10-31"], currencies=list(G10),
               preregistration="docs/research/2026-11-26-fx-styles-preregistration.md",
               data={"source": "FRED, Federal Reserve Bank of St. Louis",
                     "series": provenance(a.fred, series)})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: (v["passed"], round(v["sharpe"] or 0, 3),
                          round(v["annual_return"] or 0, 4),
                          [g for g, ok in v["gates"].items() if not ok])
                      for k, v in rep["strategies"].items()}))


if __name__ == "__main__":
    main()
