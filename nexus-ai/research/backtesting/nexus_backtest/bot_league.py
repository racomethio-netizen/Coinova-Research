"""Bot league: the NEXUS volatility-control bot against popular Binance bots, on the same
coins (BTC and ETH), period, capital and costs.

Pre-registration: docs/research/2026-11-27-bot-league-preregistration.md. Every rule is
fixed elsewhere and nothing is tuned here. The NEXUS bot's parameters come from an
earlier test that overlaps this period, so this is a side-by-side comparison, not new
proof.

Usage: ``python -m nexus_backtest.bot_league --cache DIR --out FILE``
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest import batch3
from nexus_backtest import binance_grid_bt as bg

START, END = pd.Timestamp("2021-02-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC")
CAPITAL = 10_000.0
COST = bg.FEE + bg.SLIP                      # 0.15% per traded notional, as everywhere
PREREG = "docs/research/2026-11-27-bot-league-preregistration.md"
NAMES = {
    "nexus_vol": "NEXUS volatility-control bot",
    "grid_30": "Binance Spot Grid ±30% / 30 grids",
    "grid_30_stop": "Binance Spot Grid ±30% / 30 grids + stop",
    "grid_10": "Binance Spot Grid ±10% / 40 grids",
    "rebalance": "Binance Rebalancing bot (50/50, 5-point band)",
    "hold": "Buy and hold 50/50",
}


def five_minute(cache: Path, coin: str) -> tuple[np.ndarray, np.ndarray]:
    t, ohlc = bg.load(str(cache), coin)
    sel = (t >= int(START.timestamp())) & (t < int(END.timestamp()))
    return t[sel], ohlc[sel]


def daily_close(cache: Path, coin: str) -> pd.Series:
    t, ohlc = bg.load(str(cache), coin)
    s = pd.Series(ohlc[:, 3], index=pd.to_datetime(t, unit="s", utc=True))
    return s.resample("1D").last().dropna()


def nexus_vol(btc: pd.Series, eth: pd.Series) -> pd.Series:
    """The batch3 rule as the paper test runs it: 50/50 basket, 40% target, cash before
    the start (``vol_target_ledger``), earlier days only as volatility warm-up."""
    both = pd.concat([btc, eth], axis=1).dropna()
    base = both[both.index >= START].iloc[0]
    mix = (both / base).mean(axis=1)
    led = batch3.vol_target_ledger(mix, 0.40, start=START, initial=CAPITAL, cost=COST)
    return led["equity"][led.index >= START]


def rebalance(btc: pd.Series, eth: pd.Series, band: float = 0.05) -> pd.Series:
    both = pd.concat([btc, eth], axis=1).dropna()
    both = both[both.index >= START]
    p0 = both.iloc[0].to_numpy(float)
    units = (CAPITAL / 2) * (1 - COST) / p0
    cash, out = 0.0, []
    for row in both.to_numpy(float):
        val = units * row
        eq = cash + val.sum()
        w = val[0] / val.sum()
        if abs(w - 0.5) > band:
            target = val.sum() / 2
            trade = target - val                       # + buy, - sell (USDT)
            fee = np.abs(trade).sum() * COST
            units = units + trade / row
            cash -= trade.sum() + fee
            eq = cash + (units * row).sum()
        out.append(eq)
    return pd.Series(out, index=both.index)


def hold(btc: pd.Series, eth: pd.Series) -> pd.Series:
    both = pd.concat([btc, eth], axis=1).dropna()
    both = both[both.index >= START]
    units = (CAPITAL / 2) * (1 - COST) / both.iloc[0].to_numpy(float)
    return pd.Series((both.to_numpy(float) * units).sum(axis=1), index=both.index)


def grid(cache: Path, cfg: bg.GridCfg) -> pd.Series:
    curves = []
    for coin in ("BTC", "ETH"):
        t, ohlc = five_minute(cache, coin)
        curves.append(bg.simulate(t, ohlc, cfg, alloc=CAPITAL / 2)["daily"])
    return pd.concat(curves, axis=1).ffill().sum(axis=1)


def measure(eq: pd.Series) -> dict[str, Any]:
    m = bg.stats(eq, CAPITAL)
    years = m["by_year"]
    dd = m["max_drawdown"]
    share = min(1.0, 0.05 / abs(dd)) if dd < 0 else 1.0
    m.update(worst_year=min(years.values()) if years else None,
             positive_years=sum(1 for v in years.values() if v > 0), years=len(years),
             nexus_fit={"allocation_share_for_5pct_drawdown": share,
                        "net_return_at_that_share": share * m["net_return"],
                        "usdt_on_10000_account": CAPITAL * share * m["net_return"]})
    return m


def run(cache: Path) -> dict[str, Any]:
    btc, eth = daily_close(cache, "BTC"), daily_close(cache, "ETH")
    by = {c.name: c for c in bg.CONFIGS}
    curves = {
        "nexus_vol": nexus_vol(btc, eth),
        "grid_30": grid(cache, by["±30% / 30 grids"]),
        "grid_30_stop": grid(cache, by["±30% / 30 grids + stop"]),
        "grid_10": grid(cache, by["±10% / 40 grids"]),
        "rebalance": rebalance(btc, eth),
        "hold": hold(btc, eth),
    }
    rows = {k: {"name": NAMES[k]} | measure(v) for k, v in curves.items()}
    ranking = sorted(rows, key=lambda k: -(rows[k]["sharpe"] or -9))
    return {"contenders": rows, "ranking_by_sharpe": ranking,
            "equity_monthly": {k: {str(d.date()): round(float(x), 2) for d, x in
                                   v.resample("ME").last().items()}
                               for k, v in curves.items()}}


def render(rep: dict[str, Any]) -> str:
    def pct(x: Any) -> str:
        return "—" if x is None else f"{100 * float(x):+.1f}%"

    def num(x: Any) -> str:
        return "—" if x is None else f"{float(x):.2f}"

    out = ["# Bot league: NEXUS against popular Binance bots", "",
           f"Generated {rep['generated_at']} (commit `{rep.get('harness_commit')}`). BTC + ETH, "
           f"10,000 USDT, 2021-02-01 to 2026-10-31, 0.15% per trade, Binance 5-minute data. "
           f"Pre-registration: `{PREREG}`. A side-by-side comparison of fixed rules, not new "
           "proof of an edge.", "",
           "| Rank | Bot | Net | Per year | Sharpe | Max drawdown | Calmar | Worst year | "
           "Positive years | Under the 5% NEXUS rule |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for i, k in enumerate(rep["ranking_by_sharpe"], 1):
        m = rep["contenders"][k]
        fit = m["nexus_fit"]
        out.append(f"| {i} | {m['name']} | {pct(m['net_return'])} | {pct(m['cagr'])} | "
                   f"{num(m['sharpe'])} | {pct(m['max_drawdown'])} | {num(m['calmar'])} | "
                   f"{pct(m['worst_year'])} | {m['positive_years']} of {m['years']} | "
                   f"{100 * fit['allocation_share_for_5pct_drawdown']:.0f}% of the account → "
                   f"{pct(fit['net_return_at_that_share'])} |")
    out += ["", "Historical simulation only: no orders were placed, and past results do not "
                "guarantee future results.", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    rep = run(a.cache)
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               preregistration=PREREG, period=[str(START.date()), "2026-10-31"])
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    a.out.with_suffix(".md").write_text(render(rep))
    print(render(rep))


if __name__ == "__main__":
    main()
