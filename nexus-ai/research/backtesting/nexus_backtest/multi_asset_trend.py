"""Trend following on 7 forex majors and the NASDAQ-100 (multi-asset Stage 3).

Pre-registration: docs/research/2026-11-26-multi-asset-trend-preregistration.md. FRED daily
data; excess returns (FX carry from 3-month rates, the index financed at the T-bill
rate); weekly rebalancing; gross exposure capped at 1x equity; costs per asset class.

Usage: ``python -m nexus_backtest.multi_asset_trend --fred D --out F``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

# currency: (FRED spot series, inverted (quoted per USD), FRED 3-month rate)
FX: dict[str, tuple[str, bool, str]] = {
    "EUR": ("DEXUSEU", False, "IR3TIB01EZM156N"), "GBP": ("DEXUSUK", False, "IR3TIB01GBM156N"),
    "AUD": ("DEXUSAL", False, "IR3TIB01AUM156N"), "NZD": ("DEXUSNZ", False, "IR3TIB01NZM156N"),
    "JPY": ("DEXJPUS", True, "IR3TIB01JPM156N"), "CAD": ("DEXCAUS", True, "IR3TIB01CAM156N"),
    "CHF": ("DEXSZUS", True, "IR3TIB01CHM156N")}
STOCK, STOCK_SERIES, USD_RATE = "NDX", "NASDAQ100", "DTB3"
WARMUP = pd.Timestamp("2002-04-01")
START, END = pd.Timestamp("2004-01-01"), pd.Timestamp("2026-11-01")
HALF = pd.Timestamp("2016-01-01")
COST = {"fx": 0.0002, "stock": 0.0003}
LOOKBACK, VOL_WIN, SMA_WIN = 252, 60, 200
ANN = 252


# ------------------------------------------------------------------------------ data
def read_fred(d: Path, series: str) -> pd.Series:
    df = pd.read_csv(d / f"{series}.csv")
    s = pd.to_numeric(df[series], errors="coerce")
    s.index = pd.to_datetime(df["observation_date"])
    return s.dropna()


def provenance(d: Path, series: list[str]) -> dict[str, Any]:
    out = {}
    for sid in series:
        raw = (d / f"{sid}.csv").read_bytes()
        s = read_fred(d, sid)
        out[sid] = {"rows": len(s), "first": str(s.index[0].date()),
                    "last": str(s.index[-1].date()), "sha256": hashlib.sha256(raw).hexdigest()}
    return out


def build(d: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Prices (USD per unit for FX; index level), daily excess returns and the asset class
    of each column, on the union of trading days from the warm-up to the end."""
    spot = {}
    for cur, (sid, inv, _) in FX.items():
        s = read_fred(d, sid)
        spot[cur] = 1 / s if inv else s
    spot[STOCK] = read_fred(d, STOCK_SERIES)
    px = pd.DataFrame(spot)
    px = px[(px.index >= WARMUP) & (px.index < END)].sort_index().ffill()
    days = px.index.to_series().diff().dt.days.fillna(1).to_numpy()
    usd = read_fred(d, USD_RATE).reindex(px.index, method="ffill") / 100
    ex = pd.DataFrame(index=px.index)
    for cur, (_, _, rid) in FX.items():
        rf = read_fred(d, rid).reindex(px.index, method="ffill") / 100
        carry = (rf.shift(1) - usd.shift(1)) * days / 365
        ex[cur] = px[cur].pct_change() + carry
    ex[STOCK] = px[STOCK].pct_change() - usd.shift(1) * days / 365
    klass = pd.Series({c: ("stock" if c == STOCK else "fx") for c in px.columns})
    return px, ex.iloc[1:], klass


# ------------------------------------------------------------------------- strategies
def vol(ex: pd.DataFrame) -> pd.DataFrame:
    return ex.rolling(VOL_WIN, min_periods=VOL_WIN).std() * math.sqrt(ANN)


def targets_tsmom(px: pd.DataFrame, ex: pd.DataFrame) -> pd.DataFrame:
    """Sign of the past 252-day excess return, inverse-volatility, Σ|w| = 1; all known at
    the previous close (shifted one row)."""
    mom = (1 + ex).rolling(LOOKBACK, min_periods=LOOKBACK).apply(np.prod, raw=True) - 1
    raw = np.sign(mom) / vol(ex)
    return _normalise(raw.shift(1))


def targets_sma(px: pd.DataFrame, ex: pd.DataFrame) -> pd.DataFrame:
    """Long above the 200-day average, short below (FX) or flat below (the index)."""
    p = px.reindex(ex.index)
    sma = px.rolling(SMA_WIN, min_periods=SMA_WIN).mean().reindex(ex.index)
    sig = np.sign(p - sma)
    sig[STOCK] = sig[STOCK].clip(lower=0)
    raw = sig / vol(ex)
    return _normalise(raw.shift(1))


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.replace([np.inf, -np.inf], np.nan)
    gross = raw.abs().sum(axis=1)
    return raw.div(gross.where(gross > 0), axis=0).fillna(0.0)


# -------------------------------------------------------------------------- simulate
def simulate(targets: pd.DataFrame, ex: pd.DataFrame, klass: pd.Series, *,
             cost_mult: float = 1.0, start: pd.Timestamp = START,
             end: pd.Timestamp = END) -> dict[str, Any]:
    """Weekly rebalancing to targets on the first trading day of each week, weights
    drifting in between; costs on traded notional."""
    sel = (ex.index >= start) & (ex.index < end)
    r = ex.loc[sel].fillna(0.0)
    tg = targets.reindex(r.index).fillna(0.0)
    cost = klass.reindex(r.columns).map(COST).to_numpy() * cost_mult
    week = r.index.isocalendar().week.to_numpy() + 100 * r.index.isocalendar().year.to_numpy()
    w = np.zeros(r.shape[1])
    out, costs, gross = [], [], []
    contrib = np.zeros(r.shape[1])
    rv, tv = r.to_numpy(), tg.to_numpy()
    for i in range(len(r)):
        c = 0.0
        if i == 0 or week[i] != week[i - 1]:
            c = float((np.abs(tv[i] - w) * cost).sum())
            w = tv[i].copy()
        pnl = w * rv[i]
        day = float(pnl.sum()) - c
        contrib += pnl
        out.append(day)
        costs.append(c)
        gross.append(float(np.abs(w).sum()))
        w = w * (1 + rv[i]) / (1 + day) if 1 + day > 0 else np.zeros_like(w)
    daily = pd.Series(out, index=r.index)
    return {"daily": daily, "costs": float(sum(costs)),
            "avg_gross": float(np.mean(gross)) if gross else 0.0,
            "turnover_per_year": float(sum(costs) / max(len(r) / ANN, 1e-9)),
            "contrib": pd.Series(contrib, index=r.columns)}


def stats(daily: pd.Series) -> dict[str, Any]:
    eq = (1 + daily).cumprod()
    sd = float(daily.std(ddof=1))
    years = (daily.index[-1] - daily.index[0]).days / 365.25 if len(daily) > 1 else 1.0
    return {"net_return": float(eq.iloc[-1] - 1),
            "annual_return": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
            "sharpe": float(daily.mean() / sd * math.sqrt(ANN)) if sd > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "by_year": {str(y): float((1 + g).prod() - 1)
                        for y, g in daily.groupby(daily.index.year)}}


def evaluate(targets: pd.DataFrame, ex: pd.DataFrame, klass: pd.Series) -> dict[str, Any]:
    base = simulate(targets, ex, klass)
    stress = simulate(targets, ex, klass, cost_mult=2.0)
    st = stats(base["daily"])
    halves = {"2004_2015": stats(base["daily"][base["daily"].index < HALF]),
              "2016_2026": stats(base["daily"][base["daily"].index >= HALF])}
    contrib = base["contrib"].sort_values(ascending=False)
    best = str(contrib.index[0])
    return {**st, "halves": halves, "stress_2x": stats(stress["daily"]),
            "costs_sum": base["costs"], "avg_gross": base["avg_gross"],
            "contributions": {k: float(v) for k, v in contrib.items()},
            "best_instrument": best,
            "sum_without_best": float(base["daily"].sum() - contrib.iloc[0]),
            "_daily": base["daily"]}


def report(results: dict[str, dict[str, Any]], hold: dict[str, Any]) -> dict[str, Any]:
    pvals = {k: stationary_bootstrap_pvalue(v["_daily"].to_numpy(), simulations=5000,
                                            block=10) for k, v in results.items()}
    sig = holm(pvals)
    out: dict[str, Any] = {}
    for k, v in results.items():
        yrs = v["by_year"].values()
        g = {"net_positive": v["net_return"] > 0,
             "sharpe_ge_0_5": (v["sharpe"] or 0) >= 0.5,
             "significant_holm": bool(sig[k]),
             "drawdown_le_20pct": v["max_drawdown"] >= -0.20,
             "positive_both_halves": all(h["net_return"] > 0 for h in v["halves"].values()),
             "positive_60pct_of_years": sum(1 for x in yrs if x > 0) >= 0.6 * len(yrs),
             "positive_at_2x_costs": v["stress_2x"]["net_return"] > 0,
             "positive_without_best": v["sum_without_best"] > 0}
        out[k] = {**{x: y for x, y in v.items() if not x.startswith("_")}, "p_value": pvals[k],
                  "gates": g, "passed": all(g.values())}
    return {"strategies": out, "ndx_price_hold": hold,
            "summary": {"passed": [k for k, v in out.items() if v["passed"]]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    px, ex, klass = build(a.fred)
    results = {"tsmom_12m": evaluate(targets_tsmom(px, ex), ex, klass),
               "sma_200": evaluate(targets_sma(px, ex), ex, klass)}
    ndx = px[STOCK][(px.index >= START) & (px.index < END)].pct_change().dropna()
    rep = report(results, stats(ndx))
    series = [v[0] for v in FX.values()] + [v[2] for v in FX.values()] + [STOCK_SERIES,
                                                                          USD_RATE]
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               window=[str(START.date()), "2026-10-31"],
               preregistration="docs/research/2026-11-26-multi-asset-trend-preregistration.md",
               data={"source": "FRED, Federal Reserve Bank of St. Louis",
                     "series": provenance(a.fred, series)})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: (v["passed"], v["sharpe"], v["annual_return"],
                          [g for g, ok in v["gates"].items() if not ok])
                      for k, v in rep["strategies"].items()}, default=str))


if __name__ == "__main__":
    main()
