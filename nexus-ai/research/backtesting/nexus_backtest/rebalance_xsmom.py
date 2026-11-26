"""Rebalancing bot vs buy-and-hold, and long-only cross-sectional momentum.

Pre-registration: docs/research/2026-11-26-rebalancing-xsmom-preregistration.md.
Daily closes at 00:00 UTC from verified Binance 5m data. Costs: 0.10% fee + 0.05%
slippage per side on traded notional (2x for the stress test). Spot only, long only, no
leverage.

Usage: ``python -m nexus_backtest.rebalance_xsmom --data DIR --out FILE``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

COINS = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "LTC")
START, END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC")
COST = 0.0015                       # fee + slippage per side
BASKETS = {"R1_BTC50_ETH50": {"BTC": 0.5, "ETH": 0.5},
           "R2_equal10": {c: 0.1 for c in COINS},
           "R3_BTC40_ETH40_USDT20": {"BTC": 0.4, "ETH": 0.4, "USDT": 0.2}}
BAND = 0.05
LOOKBACKS = (1, 2, 4)
TOP = 3


def load_closes(data: Path) -> pd.DataFrame:
    """Daily closes (the close of the last hourly bar of each UTC day)."""
    out = {}
    for c in COINS:
        h = pd.read_csv(data / f"{c}_1h.csv.gz", parse_dates=["date"]).set_index("date")
        out[c] = h["close"].resample("1D").last()
    return pd.DataFrame(out).dropna()


# ------------------------------------------------------------------------ rebalancing
def rebalance(closes: pd.DataFrame, target: dict[str, float], *, mode: str = "band",
              cost: float = COST) -> dict[str, Any]:
    """Equity of a basket rebalanced to ``target`` (threshold band or weekly), and of the
    same basket held without rebalancing. Both start fully invested on the first day."""
    px = closes.loc[(closes.index >= START) & (closes.index < END)]
    assets = list(target)
    w = np.array([target[a] for a in assets])
    prices = np.column_stack([px[a].to_numpy() if a != "USDT" else np.ones(len(px))
                              for a in assets])
    equity0 = 10_000.0
    units = equity0 * (1 - cost * (1 - target.get("USDT", 0))) * w / prices[0]
    hold_units = units.copy()
    eq, hold, trades, turnover = [], [], 0, 0.0
    for i in range(len(px)):
        val = units * prices[i]
        total = val.sum()
        cur = val / total
        due = (np.abs(cur - w).max() > BAND) if mode == "band" else \
            (px.index[i].day_of_week == 0 and i > 0)
        if due:
            new_val = total * w
            traded = np.abs(new_val - val)
            traded[[j for j, a in enumerate(assets) if a == "USDT"]] = 0
            fee = traded.sum() * cost
            total -= fee
            units = total * w / prices[i]
            trades += 1
            turnover += traded.sum()
        eq.append((units * prices[i]).sum())
        hold.append((hold_units * prices[i]).sum())
    return {"equity": pd.Series(eq, index=px.index), "hold": pd.Series(hold, index=px.index),
            "rebalances": trades, "turnover": turnover / equity0}


# ------------------------------------------------------------- cross-sectional momentum
def xsmom(closes: pd.DataFrame, lookback_w: int, market_filter: bool, *,
          cost: float = COST) -> dict[str, Any]:
    """Each Monday: hold the top ``TOP`` coins by past ``lookback_w``-week return (equal
    weight), from Monday's close to the next Monday's close. Decisions use closes up to
    and including the decision day only."""
    px = closes
    weekly = px[px.index.day_of_week == 0]
    btc_ma = px["BTC"].rolling(140, min_periods=140).mean()      # 20 weeks of days
    daily_ret = px.pct_change()
    w_now = pd.Series(0.0, index=COINS)
    weights = pd.DataFrame(0.0, index=px.index, columns=list(COINS))
    costs = pd.Series(0.0, index=px.index)
    contrib = pd.Series(0.0, index=list(COINS))
    for d in weekly.index:
        if d < START - pd.Timedelta(days=7) or d >= END:
            continue
        past = px.loc[:d]
        if len(past) <= lookback_w * 7:
            continue
        ret = past.iloc[-1] / past.iloc[-1 - lookback_w * 7] - 1
        target = pd.Series(0.0, index=COINS)
        risk_on = (not market_filter) or (not math.isnan(btc_ma.loc[d])
                                          and px.loc[d, "BTC"] > btc_ma.loc[d])
        if risk_on:
            target[ret.sort_values(ascending=False).index[:TOP]] = 1.0 / TOP
        costs.loc[d] = float((target - w_now).abs().sum()) * cost
        w_now = target
        weights.loc[d:] = target.to_numpy()
    held = weights.shift(1).fillna(0.0)                # weights act from the next day
    gross = (held * daily_ret.fillna(0.0)).sum(axis=1)
    net = gross - costs
    net = net[(net.index >= START) & (net.index < END)]
    for c in COINS:
        contrib[c] = float((held[c] * daily_ret[c].fillna(0.0))
                           .loc[net.index].sum())
    return {"returns": net, "exposure": float((held.loc[net.index].sum(axis=1) > 0).mean()),
            "turnover_per_year": float(costs.loc[net.index].sum() / cost / len(net) * 365),
            "contribution": contrib.to_dict()}


# ---------------------------------------------------------------------------- metrics
def stats(r: pd.Series) -> dict[str, Any]:
    eq = (1 + r).cumprod()
    sd = float(r.std(ddof=1))
    years = {str(y): float((1 + g).prod() - 1) for y, g in r.groupby(r.index.year)}
    return {"net_return": float(eq.iloc[-1] - 1), "sharpe": float(r.mean() / sd *
                                                                  math.sqrt(365)) if sd else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()), "by_year": years}


def eq_stats(eq: pd.Series) -> dict[str, Any]:
    return stats(eq.pct_change().fillna(eq.iloc[0] / 10_000 - 1))


def run(data: Path) -> dict[str, Any]:
    closes = load_closes(data)
    out: dict[str, Any] = {"window": [str(START.date()), str(END.date())], "cost": COST,
                           "coins": list(COINS), "rebalancing": {}, "xsmom": {}}
    for name, basket in BASKETS.items():
        for mode in ("band", "weekly"):
            r = rebalance(closes, basket, mode=mode)
            r2 = rebalance(closes, basket, mode=mode, cost=2 * COST)
            s, h = eq_stats(r["equity"]), eq_stats(r["hold"])
            ex_years = {y: s["by_year"][y] - h["by_year"].get(y, 0) for y in s["by_year"]}
            gates = {
                "beats_hold": s["net_return"] > h["net_return"],
                "excess_2_of_3_years": sum(1 for v in ex_years.values() if v > 0) >= 2,
                "beats_hold_at_2x_costs": eq_stats(r2["equity"])["net_return"]
                > h["net_return"],
                "drawdown_within_5pts": s["max_drawdown"] >= h["max_drawdown"] - 0.05}
            out["rebalancing"][f"{name}:{mode}"] = {
                "strategy": s, "buy_and_hold": h, "excess_by_year": ex_years,
                "rebalances": r["rebalances"], "turnover": r["turnover"],
                "gates": gates, "passed": mode == "band" and all(gates.values())}
    pvals, rows = {}, {}
    for lb in LOOKBACKS:
        for filt in (False, True):
            key = f"L{lb}w{'_filter' if filt else ''}"
            res = xsmom(closes, lb, filt)
            res2 = xsmom(closes, lb, filt, cost=2 * COST)
            s = stats(res["returns"])
            pvals[key] = stationary_bootstrap_pvalue(res["returns"].to_numpy(),
                                                     simulations=5000, block=10)
            rows[key] = {"strategy": s, "sharpe_2x_costs": stats(res2["returns"])["sharpe"],
                         "exposure": res["exposure"],
                         "turnover_per_year": res["turnover_per_year"],
                         "contribution": res["contribution"], "p_value": pvals[key]}
    win = closes.loc[(closes.index >= START) & (closes.index < END)]
    ew_eq = (win / win.iloc[0]).mean(axis=1)             # equal amounts bought on day one
    ew_stats = stats(ew_eq.pct_change().fillna(0.0))
    sig = holm(pvals)
    for key, row in rows.items():
        s = row["strategy"]
        gates = {"sharpe_ge_0_5": (s["sharpe"] or 0) >= 0.5,
                 "holm_significant": sig[key],
                 "drawdown_le_35pct": s["max_drawdown"] >= -0.35,
                 "positive_2_of_3_years": sum(1 for v in s["by_year"].values() if v > 0) >= 2,
                 "sharpe_positive_2x_costs": (row["sharpe_2x_costs"] or 0) > 0,
                 "beats_equal_weight_sharpe": (s["sharpe"] or 0) > (ew_stats["sharpe"] or 0)}
        row["gates"] = gates
        row["passed"] = all(gates.values())
    out["xsmom"] = rows
    out["equal_weight_hold"] = ew_stats
    btc = closes["BTC"].loc[(closes.index >= START) & (closes.index < END)]
    out["btc_hold"] = stats(btc.pct_change().fillna(0.0))
    out["summary"] = {
        "rebalancing_passed": [k for k, v in out["rebalancing"].items() if v["passed"]],
        "xsmom_passed": [k for k, v in rows.items() if v["passed"]]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    res = run(a.data)
    a.out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["summary"]))


if __name__ == "__main__":
    main()
