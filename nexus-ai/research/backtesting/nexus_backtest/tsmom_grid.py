"""Time-series momentum grid (Han, Kang & Ryu design), raw and risk-managed.

Pre-registered in docs/research/2026-11-23-tsmom-grid-preregistration.md. Daily perpetual
closes and funding. No look-ahead:
* signals use closes up to day t;
* the position for day t+1 is the average of the h live tranches formed at t, t-1, ...;
* it earns day t+1's close-to-close return;
* the volatility scale for day t+1 uses strategy returns up to day t only.

    python -m nexus_backtest.tsmom_grid --cache DIR --out FILE
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOOKBACKS: tuple[int | str, ...] = (7, 14, 28, "ENS")
HOLDS = (1, 5, 7, 14)
DIRECTIONS = ("long", "short", "long_short")
COST_PER_SIDE = 0.0007            # 5 bp taker + 2 bp slippage
TARGET_VOL = 0.20
VOL_WINDOW = 30
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
           "LINKUSDT", "AVAXUSDT", "LTCUSDT")
FIRST_MONTH = {"BNBUSDT": "2020-03", "DOGEUSDT": "2020-08", "SOLUSDT": "2020-10",
               "AVAXUSDT": "2020-10"}
IS_PERIOD = (pd.Timestamp("2020-03-01", tz="UTC"), pd.Timestamp("2023-01-01", tz="UTC"))
OOS_PERIOD = (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC"))


# ------------------------------------------------------------------ strategy
def signal(close: pd.DataFrame, lookback: int | str) -> pd.DataFrame:
    """Sign of the past-L-day return per coin, known at day t's close (NaN = no history).
    ENS averages the 7, 14 and 28-day signs."""
    if lookback == "ENS":
        parts = [signal(close, lb) for lb in (7, 14, 28)]
        return sum(parts) / 3
    assert isinstance(lookback, int)
    past = close.shift(lookback)
    return np.sign(close / past - 1).where(past.notna())


def target_weights(sig: pd.DataFrame, direction: str) -> pd.DataFrame:
    """One day's tranche: equal weight over the coins with a signal; gross <= 1."""
    s = sig.copy()
    if direction == "long":
        s = s.clip(lower=0)
    elif direction == "short":
        s = s.clip(upper=0)
    n = sig.notna().sum(axis=1).replace(0, np.nan)
    return s.div(n, axis=0).fillna(0.0)


def held(weights: pd.DataFrame, hold: int) -> pd.DataFrame:
    """Position DURING day t+1: the mean of the tranches formed at t, t-1, ..., t-h+1."""
    return weights.rolling(hold, min_periods=1).mean().shift(1).fillna(0.0)


def pnl(pos: pd.DataFrame, close: pd.DataFrame, funding: pd.DataFrame,
        cost: float = COST_PER_SIDE) -> pd.DataFrame:
    """Daily gross, cost, funding and net returns of a position frame."""
    ret = close.pct_change().reindex_like(pos).fillna(0.0)
    gross = (pos * ret).sum(axis=1)
    turnover = pos.diff().abs().sum(axis=1).fillna(pos.abs().sum(axis=1))
    fund = (pos * funding.reindex_like(pos).fillna(0.0)).sum(axis=1)   # long pays positive
    net = gross - turnover * cost - fund
    return pd.DataFrame({"gross": gross, "costs": turnover * cost, "funding": fund,
                         "net": net, "turnover": turnover,
                         "exposure": pos.abs().sum(axis=1)})


def risk_managed(pos: pd.DataFrame, raw_net: pd.Series) -> pd.DataFrame:
    """Scale day t+1's position by min(1, target / realised vol up to day t)."""
    vol = raw_net.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std() * math.sqrt(365)
    scale = (TARGET_VOL / vol).clip(upper=1.0).shift(1).fillna(0.0)
    return pos.mul(scale, axis=0)


def run_config(close: pd.DataFrame, funding: pd.DataFrame, lookback: int | str, hold: int,
               direction: str, managed: bool, cost: float = COST_PER_SIDE) -> pd.DataFrame:
    pos = held(target_weights(signal(close, lookback), direction), hold)
    res = pnl(pos, close, funding, cost)
    if managed:
        res = pnl(risk_managed(pos, res["net"]), close, funding, cost)
    return res


# ------------------------------------------------------------------ statistics
def stats(net: pd.Series) -> dict[str, Any]:
    r = net.dropna()
    if len(r) < 30:
        return {"days": len(r)}
    eq = (1 + r).cumprod()
    mdd = float((1 - eq / eq.cummax()).max())
    sd = r.std()
    down = r[r < 0].std()
    years = len(r) / 365
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if eq.iloc[-1] > 0 else -1.0
    return {"days": len(r), "total_return": float(eq.iloc[-1] - 1), "cagr": cagr,
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "sortino": float(r.mean() / down * math.sqrt(365)) if down > 0 else None,
            "max_drawdown": mdd, "calmar": cagr / mdd if mdd > 0 else None,
            "mean_daily": float(r.mean())}


def by_year(net: pd.Series) -> dict[str, float]:
    return {str(y): float((1 + g).prod() - 1) for y, g in net.groupby(net.index.year)}


def pbo_cscv(matrix: pd.DataFrame, blocks: int = 10) -> float | None:
    """Probability of backtest overfitting (Bailey et al., CSCV): split time into blocks;
    for every half / half split, does the in-sample best rank below the median out of
    sample?"""
    m = matrix.dropna(how="all").fillna(0.0)
    if m.shape[1] < 2 or len(m) < blocks * 20:
        return None
    parts = np.array_split(np.arange(len(m)), blocks)
    below = total = 0
    for combo in itertools.combinations(range(blocks), blocks // 2):
        is_idx = np.concatenate([parts[i] for i in combo])
        oos_idx = np.concatenate([parts[i] for i in range(blocks) if i not in combo])

        def sharpe(idx: np.ndarray) -> pd.Series:
            x = m.iloc[idx]
            return x.mean() / x.std().replace(0, np.nan)

        best = sharpe(is_idx).idxmax()
        oos_rank = sharpe(oos_idx).rank(pct=True)[best]
        below += int(oos_rank < 0.5)
        total += 1
    return below / total if total else None


# ------------------------------------------------------------------ data
async def load(cache: Path, end: str = "2026-10") -> tuple[pd.DataFrame, pd.DataFrame]:
    from nexus_backtest.tsmom import load_daily

    closes, funds = {}, {}
    for sym in SYMBOLS:
        c, f = await load_daily(sym, FIRST_MONTH.get(sym, "2020-01"), end, cache)
        closes[sym], funds[sym] = c, f
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(funds).sort_index()


def verify_checksums(cache: Path) -> dict[str, int]:
    """SHA-256 of every cached archive against Binance's published .CHECKSUM files."""
    import httpx

    from nexus_backtest.futures_data import BASE

    ok = bad = missing = 0
    with httpx.Client(follow_redirects=True, trust_env=True, timeout=30) as client:
        for z in sorted(cache.glob("*.zip")):
            name = z.name
            sym, kind = name.split("-")[0], name.split("-")[1]
            sub = f"klines/{sym}/1d" if kind == "1d" else f"fundingRate/{sym}"
            ck = cache / (name + ".CHECKSUM")
            if not ck.exists():
                r = client.get(f"{BASE}/{sub}/{name}.CHECKSUM")
                if r.status_code != 200:
                    missing += 1
                    continue
                ck.write_bytes(r.content)
            expected = ck.read_text().split()[0]
            if hashlib.sha256(z.read_bytes()).hexdigest() == expected:
                ok += 1
            else:
                bad += 1
    return {"ok": ok, "bad": bad, "no_checksum_published": missing}


# ------------------------------------------------------------------ run
def main() -> None:
    from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cache = Path(a.cache)
    close, funding = asyncio.run(load(cache))
    checks = verify_checksums(cache)
    if checks["bad"]:
        raise SystemExit(f"checksum mismatch: {checks}")
    close = close[close.index < OOS_PERIOD[1]]
    funding = funding.reindex(close.index).fillna(0.0)
    results: dict[str, Any] = {"checksums": checks, "data": {
        s: [str(close[s].first_valid_index()), str(close[s].last_valid_index()),
            int(close[s].notna().sum())] for s in close}, "configs": {}}
    nets: dict[str, pd.Series] = {}
    for lb, h, d, mg in itertools.product(LOOKBACKS, HOLDS, DIRECTIONS, (False, True)):
        key = f"L{lb}_H{h}_{d}_{'managed' if mg else 'raw'}"
        res = run_config(close, funding, lb, h, d, mg)
        net = res["net"][res.index >= IS_PERIOD[0]]
        nets[key] = net
        is_net = net[net.index < IS_PERIOD[1]]
        oos = res[(res.index >= OOS_PERIOD[0]) & (res.index < OOS_PERIOD[1])]
        x2 = run_config(close, funding, lb, h, d, mg, cost=2 * COST_PER_SIDE)
        x3 = run_config(close, funding, lb, h, d, mg, cost=3 * COST_PER_SIDE)
        oos_mask = (x2.index >= OOS_PERIOD[0]) & (x2.index < OOS_PERIOD[1])
        contrib = {}
        for sym in close.columns:              # remove each coin in turn
            r = run_config(close.drop(columns=sym), funding.drop(columns=sym), lb, h, d, mg)
            contrib[sym] = stats(r["net"][oos_mask])["sharpe"]
        best_coin = min(contrib, key=lambda k: contrib[k] if contrib[k] is not None else 9)
        results["configs"][key] = {
            "is": stats(is_net), "oos": stats(oos["net"]),
            "oos_by_year": by_year(oos["net"]),
            "oos_2x_cost": stats(x2["net"][oos_mask]), "oos_3x_cost": stats(x3["net"][oos_mask]),
            "oos_costs": float(oos["costs"].sum()), "oos_funding": float(oos["funding"].sum()),
            "oos_exposure": float(oos["exposure"].mean()),
            "oos_turnover": float(oos["turnover"].mean()),
            "without_best_coin": {"coin": best_coin, "sharpe": contrib[best_coin]},
            "p_value": stationary_bootstrap_pvalue(oos["net"].to_numpy(), simulations=5000)}
        print(key, results["configs"][key]["oos"].get("sharpe"), flush=True)
    sig = holm({k: v["p_value"] for k, v in results["configs"].items()})
    for k, v in results["configs"].items():
        o, yrs = v["oos"], v["oos_by_year"]
        v["holm_significant"] = sig[k]
        v["gates"] = {
            "sharpe_ge_0.5": (o.get("sharpe") or -9) >= 0.5,
            "holm_significant": sig[k] and (o.get("mean_daily") or 0) > 0,
            "max_drawdown_le_25pct": o.get("max_drawdown", 1) <= 0.25,
            "positive_3_of_4_years": sum(1 for x in yrs.values() if x > 0) >= 3,
            "positive_at_2x_costs": (v["oos_2x_cost"].get("sharpe") or -9) > 0,
            "not_one_coin": (v["without_best_coin"]["sharpe"] or -9) > 0}
        v["passed"] = all(v["gates"].values())
    best_is = max(results["configs"], key=lambda k: results["configs"][k]["is"].get("sharpe")
                  or -9)
    results["best_in_sample"] = best_is
    results["passed"] = sorted(k for k, v in results["configs"].items() if v["passed"])
    results["pbo"] = pbo_cscv(pd.DataFrame(nets))
    # Benchmarks over the OOS window.
    oos_close = close[(close.index >= OOS_PERIOD[0]) & (close.index < OOS_PERIOD[1])]
    ew = oos_close.pct_change().mean(axis=1).fillna(0)
    results["benchmarks"] = {"equal_weight_hold": stats(ew),
                             "btc_hold": stats(oos_close["BTCUSDT"].pct_change().fillna(0))}
    up = close.pct_change()
    results["worst_day_move"] = {"long_side_min": float(up.min().min()),
                                 "short_side_max": float(up.max().max())}
    Path(a.out).write_text(json.dumps(results, indent=1, default=str))
    print("best IS:", best_is, "PASSED:", results["passed"] or "none", "PBO:", results["pbo"])


if __name__ == "__main__":
    main()
