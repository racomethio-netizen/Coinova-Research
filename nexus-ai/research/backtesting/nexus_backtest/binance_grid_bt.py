"""Binance Spot Grid bot as ordinary users run it, on Binance 5-minute klines.

Pre-registration: docs/research/2026-11-27-binance-spot-grid-preregistration.md.

* **Grid.** N geometric grids in a fixed range around the start price. Each grid trades
  the same base quantity: it buys at its lower line and sells at its upper line, then
  places the opposite order again.
* **Start.** Grids above the price are bought at market, so they hold coins with a sell
  order waiting.
* **Life.** A bot lives 30 days; then its coins are sold at market and a new bot starts
  around the new price.
* **Fills.** Trade-through only, at the line price, with the opposite order active from
  the next bar. Fees are 0.1% per fill, plus 0.05% slippage on market orders.

Usage:
* ``python -m nexus_backtest.binance_grid_bt download --cache DIR``
* ``python -m nexus_backtest.binance_grid_bt run --cache DIR --out FILE``
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.freqtrade_community import _client, months, parse_kline_zip
from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

BASE = "https://data.binance.vision/data/spot/monthly/klines"
COINS = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "LTC")
FIRST, LAST = "2021-01", "2026-10"
ALLOC = 1000.0
FEE, SLIP = 0.001, 0.0005
LIFE = 30 * 86400
PREREG = "docs/research/2026-11-27-binance-spot-grid-preregistration.md"


@dataclass(frozen=True)
class GridCfg:
    name: str
    width: float              # range = start price x (1 +/- width)
    grids: int
    stop: bool
    stop_below: float = 0.05  # stop this far below the lower line


CONFIGS = (GridCfg("±10% / 40 grids", 0.10, 40, False),
           GridCfg("±10% / 40 grids + stop", 0.10, 40, True),
           GridCfg("±20% / 40 grids", 0.20, 40, False),
           GridCfg("±20% / 40 grids + stop", 0.20, 40, True),
           GridCfg("±30% / 30 grids", 0.30, 30, False),
           GridCfg("±30% / 30 grids + stop", 0.30, 30, True))


# ------------------------------------------------------------------------------ data
def download(cache: Path, coins: tuple[str, ...] = COINS, first: str = FIRST,
             last: str = LAST, workers: int = 8) -> dict[str, int]:
    """Every monthly 5m archive (cached), checked against Binance's SHA-256 file."""
    cache.mkdir(parents=True, exist_ok=True)
    jobs = [(f"{c}USDT", mo) for c in coins for mo in months(first, last)]

    def one(job: tuple[str, str]) -> str:
        sym, mo = job
        name = f"{sym}-5m-{mo}.zip"
        path, ck = cache / name, cache / (name + ".CHECKSUM")
        with _client() as c:
            if not path.exists():
                r = c.get(f"{BASE}/{sym}/5m/{name}")
                if r.status_code != 200:
                    raise RuntimeError(f"missing archive {name}: HTTP {r.status_code}")
                path.write_bytes(r.content)
            if not ck.exists():
                r = c.get(f"{BASE}/{sym}/5m/{name}.CHECKSUM")
                if r.status_code == 200:
                    ck.write_text(r.text)
        if not ck.exists():
            return "no_checksum"
        if hashlib.sha256(path.read_bytes()).hexdigest() != ck.read_text().split()[0]:
            path.unlink()
            raise RuntimeError(f"checksum mismatch: {name}")
        return "checksum_ok"

    stats: dict[str, int] = {"checksum_ok": 0, "no_checksum": 0}
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(one, jobs):
            stats[res] += 1
    return stats


@lru_cache(maxsize=2)
def load(cache: str, coin: str, first: str = FIRST, last: str = LAST
         ) -> tuple[np.ndarray, np.ndarray]:
    """(unix seconds, OHLC array n x 4), sorted, de-duplicated. Nothing is filled."""
    parts = [parse_kline_zip((Path(cache) / f"{coin}USDT-5m-{mo}.zip").read_bytes())
             for mo in months(first, last)]
    df = pd.concat(parts).drop_duplicates("date").sort_values("date")
    # Unit-agnostic: pandas may store these as ns, us or ms.
    t = ((df["date"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(
        dtype=np.int64)
    return t, df[["open", "high", "low", "close"]].to_numpy(dtype=float)


# ------------------------------------------------------------------------- the bot
def lines(p0: float, cfg: GridCfg) -> list[float]:
    lower, upper = p0 * (1 - cfg.width), p0 * (1 + cfg.width)
    r = (upper / lower) ** (1 / cfg.grids)
    return [lower * r ** k for k in range(cfg.grids + 1)]


def bounds(L: list[float], held: list[bool], active: list[int], b: int
           ) -> tuple[float, float]:
    """The highest active buy line and the lowest active sell line at bar ``b``."""
    n = len(held)
    hb = max((L[k] for k in range(n) if not held[k] and active[k] <= b), default=-1.0)
    ls = min((L[k + 1] for k in range(n) if held[k] and active[k] <= b), default=math.inf)
    return hb, ls


def simulate(t: np.ndarray, ohlc: np.ndarray, cfg: GridCfg, *, fee: float = FEE,
             slip: float = SLIP, alloc: float = ALLOC) -> dict[str, Any]:
    """One coin, consecutive 30-day bots. Returns end-of-day equity and statistics."""
    o, h, lo, c = (ohlc[:, k].tolist() for k in range(4))
    ts = t.tolist()
    day = (t // 86400).tolist()
    n = len(ts)
    equity, i = alloc, 0
    marks: dict[int, float] = {}
    trips = stops = bots = below = above = 0
    fees = 0.0
    while i < n:
        bots += 1
        end_i = int(np.searchsorted(t, ts[i] + LIFE)) - 1
        p0 = o[i]
        L = lines(p0, cfg)
        N = cfg.grids
        held = [L[k] >= p0 for k in range(N)]              # grid holds coins, sell waiting
        buy_px = (1 + slip) * p0
        n_held = sum(held)
        cost_buys = sum(L[k] for k in range(N) if not held[k]) * (1 + fee)
        q = equity / (cost_buys + n_held * buy_px * (1 + fee))
        base = n_held * q
        quote = equity - n_held * q * buy_px * (1 + fee)
        fees += n_held * q * buy_px * fee
        active = [i] * N
        stop_px = L[0] * (1 - cfg.stop_below) if cfg.stop else -1.0
        stopped = False
        hb, ls = bounds(L, held, active, i)
        dirty = False
        for b in range(i, end_i + 1):
            if not stopped:
                if dirty:
                    hb, ls = bounds(L, held, active, b)
                    dirty = False
                if lo[b] < hb or h[b] > ls or lo[b] < stop_px:
                    # An order that is already marketable when it becomes active (the bar
                    # opens beyond its line) fills at once at the open, as a real limit
                    # order placed through the market does: never at the worse line price.
                    for k in range(N):
                        if active[k] != b:
                            continue
                        if not held[k] and o[b] < L[k]:
                            quote -= q * o[b] * (1 + fee)
                            fees += q * o[b] * fee
                            base += q
                            held[k], active[k] = True, b + 1
                            dirty = True
                        elif held[k] and o[b] > L[k + 1]:
                            quote += q * o[b] * (1 - fee)
                            fees += q * o[b] * fee
                            base -= q
                            held[k], active[k] = False, b + 1
                            trips += 1
                            dirty = True
                    path = (o[b], lo[b], h[b], c[b]) if c[b] >= o[b] else \
                        (o[b], h[b], lo[b], c[b])
                    for a, z in itertools.pairwise(path):
                        if z < a:
                            for k in range(N - 1, -1, -1):
                                if not held[k] and active[k] <= b and L[k] > z:
                                    quote -= q * L[k] * (1 + fee)
                                    fees += q * L[k] * fee
                                    base += q
                                    held[k], active[k] = True, b + 1
                                    dirty = True
                            if z < stop_px:
                                px = stop_px * (1 - slip)
                                quote += base * px * (1 - fee)
                                fees += base * px * fee
                                base, stopped = 0.0, True
                                stops += 1
                                break
                        elif z > a:
                            for k in range(N):
                                if held[k] and active[k] <= b and L[k + 1] < z:
                                    quote += q * L[k + 1] * (1 - fee)
                                    fees += q * L[k + 1] * fee
                                    base -= q
                                    held[k], active[k] = False, b + 1
                                    trips += 1
                                    dirty = True
            if c[b] < L[0]:
                below += 1
            elif c[b] > L[-1]:
                above += 1
            if b == end_i:                                   # the bot ends: sell at market
                px = c[b] * (1 - slip)
                quote += base * px * (1 - fee)
                fees += base * px * fee
                base = 0.0
            if b == n - 1 or day[b + 1] != day[b] or b == end_i:
                marks[day[b]] = quote + base * c[b]
        equity, i = quote, end_i + 1
    daily = pd.Series(marks)
    daily.index = pd.to_datetime(daily.index * 86400, unit="s", utc=True)
    return {"daily": daily.sort_index(), "trips": trips, "fees": fees, "stops": stops,
            "bots": bots, "below_range_share": below / n, "above_range_share": above / n}


def hold(t: np.ndarray, ohlc: np.ndarray, *, fee: float = FEE, slip: float = SLIP,
         alloc: float = ALLOC) -> pd.Series:
    units = alloc / (ohlc[0, 0] * (1 + slip) * (1 + fee))
    s = pd.Series(units * ohlc[:, 3], index=pd.to_datetime(t, unit="s", utc=True))
    return s.resample("1D").last().dropna()


# ----------------------------------------------------------------------- evaluation
def stats(eq: pd.Series, start: float) -> dict[str, Any]:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    growth = float(eq.iloc[-1] / start)
    cagr = growth ** (1 / years) - 1 if growth > 0 else -1.0
    dd = float((eq / np.maximum(eq.cummax(), start) - 1).min())
    sd = float(r.std())
    halves = eq.groupby([eq.index.year, (eq.index.month > 6).astype(int)]).last()
    prev, hy = start, {}
    for (y, hh), v in halves.items():
        hy[f"{y}-H{hh + 1}"] = float(v / prev - 1)
        prev = float(v)
    yearly = eq.groupby(eq.index.year).last()
    prev, by_year = start, {}
    for y, v in yearly.items():
        by_year[str(y)] = float(v / prev - 1)
        prev = float(v)
    return {"net_return": growth - 1, "cagr": cagr, "max_drawdown": dd,
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "calmar": cagr / abs(dd) if dd < 0 else None, "half_years": hy,
            "by_year": by_year, "days": len(eq)}


def _job(args: tuple[str, str, GridCfg, float]) -> tuple[str, str, float, dict[str, Any]]:
    cache, coin, cfg, fee_mult = args
    t, ohlc = load(cache, coin)
    res = simulate(t, ohlc, cfg, fee=FEE * fee_mult, slip=SLIP * fee_mult)
    return coin, cfg.name, fee_mult, res


def gates(m: dict[str, Any], hold_m: dict[str, Any], stress: dict[str, Any],
          without_best: float, sig: bool) -> dict[str, bool]:
    hy = list(m["half_years"].values())
    return {"net_positive_after_fees": m["net_return"] > 0,
            "sharpe_ge_0_5": (m["sharpe"] or 0) >= 0.5,
            "sharpe_above_holding": (m["sharpe"] or -9) > (hold_m["sharpe"] or -9),
            "calmar_at_least_holding": (m["calmar"] or -9) >= (hold_m["calmar"] or -9),
            "positive_in_60pct_of_half_years": bool(hy) and
                float(np.mean([x > 0 for x in hy])) >= 0.6,
            "positive_at_2x_fees": stress["net_return"] > 0,
            "positive_without_best_coin": without_best > 0,
            "significant_after_holm": sig}


def run(cache: Path, workers: int = 4) -> dict[str, Any]:
    jobs = [(str(cache), coin, cfg, fm) for cfg in CONFIGS for coin in COINS
            for fm in (1.0, 2.0)]
    res: dict[tuple[str, str, float], dict[str, Any]] = {}
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as ex:
        for coin, name, fm, r in ex.map(_job, jobs):
            res[(coin, name, fm)] = r
    holds = {}
    for coin in COINS:
        t, ohlc = load(str(cache), coin)
        holds[coin] = hold(t, ohlc)
    hold_pf = pd.concat(holds, axis=1).ffill().sum(axis=1)
    hold_m = stats(hold_pf, ALLOC * len(COINS))
    out: dict[str, Any] = {"configs": {}, "holding": hold_m,
                           "holding_by_coin": {c: float(s.iloc[-1] / ALLOC - 1)
                                               for c, s in holds.items()}}
    pvals = {}
    for cfg in CONFIGS:
        curves = {c: res[(c, cfg.name, 1.0)]["daily"] for c in COINS}
        pf = pd.concat(curves, axis=1).ffill().sum(axis=1)
        m = stats(pf, ALLOC * len(COINS))
        stress_pf = pd.concat({c: res[(c, cfg.name, 2.0)]["daily"] for c in COINS},
                              axis=1).ffill().sum(axis=1)
        stress = stats(stress_pf, ALLOC * len(COINS))
        finals = {c: float(s.iloc[-1]) for c, s in curves.items()}
        best = max(finals, key=lambda k: finals[k])
        rest = sum(v for k, v in finals.items() if k != best)
        without_best = rest / (ALLOC * (len(COINS) - 1)) - 1
        r = pf.pct_change().dropna().to_numpy()
        p = stationary_bootstrap_pvalue(r, simulations=5000, block=10)
        pvals[cfg.name] = p
        alloc_share = min(1.0, 0.05 / abs(m["max_drawdown"])) if m["max_drawdown"] < 0 else 1.0
        out["configs"][cfg.name] = {
            "config": asdict(cfg), "metrics": m, "p_value": p,
            "stress_2x_fees": {k: stress[k] for k in ("net_return", "sharpe", "max_drawdown")},
            "without_best_coin": {"best": best, "net_return": without_best},
            "by_coin": {c: {"net_return": finals[c] / ALLOC - 1,
                            "trips": res[(c, cfg.name, 1.0)]["trips"],
                            "fees_usdt": res[(c, cfg.name, 1.0)]["fees"],
                            "stops": res[(c, cfg.name, 1.0)]["stops"],
                            "below_range_share": res[(c, cfg.name, 1.0)]["below_range_share"],
                            "above_range_share": res[(c, cfg.name, 1.0)]["above_range_share"]}
                        for c in COINS},
            "fees_usdt": sum(res[(c, cfg.name, 1.0)]["fees"] for c in COINS),
            "trips": sum(res[(c, cfg.name, 1.0)]["trips"] for c in COINS),
            "nexus_fit": {"allocation_share_for_5pct_drawdown": alloc_share,
                          "net_return_at_that_share": alloc_share * m["net_return"]},
            "_stress": stress, "_without_best": without_best}
    flags = holm(pvals)
    for name, v in out["configs"].items():
        v["gates"] = gates(v["metrics"], hold_m, v.pop("_stress"), v.pop("_without_best"),
                           flags.get(name, False))
        v["passed"] = all(v["gates"].values())
    return out


def render(rep: dict[str, Any]) -> str:
    def pct(x: Any) -> str:
        return "—" if x is None else f"{100 * float(x):+.1f}%"

    def num(x: Any) -> str:
        return "—" if x is None else f"{float(x):.2f}"

    hm = rep["holding"]
    out = ["# Binance Spot Grid bot: test report", "",
           f"Generated {rep['generated_at']} (harness commit `{rep.get('harness_commit')}`). "
           f"{rep['kind']}. 10 coins x 1,000 USDT, Binance 5-minute data "
           f"{FIRST} to {LAST}. Pre-registration: `{PREREG}`.", "",
           f"**Holding the same coins:** net {pct(hm['net_return'])}, Sharpe {num(hm['sharpe'])}, "
           f"max drawdown {pct(hm['max_drawdown'])}, Calmar {num(hm['calmar'])}.", "",
           "| Configuration | Net | Sharpe | Max drawdown | Calmar | 2x fees | Trades | "
           "Fees (USDT) | Verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for name, v in rep["configs"].items():
        m = v["metrics"]
        out.append(f"| {name} | {pct(m['net_return'])} | {num(m['sharpe'])} | "
                   f"{pct(m['max_drawdown'])} | {num(m['calmar'])} | "
                   f"{pct(v['stress_2x_fees']['net_return'])} | {v['trips']} | "
                   f"{v['fees_usdt']:.0f} | {'PASSED' if v['passed'] else 'FAILED'} |")
    out += ["", "## Failed checks", ""]
    for name, v in rep["configs"].items():
        failed = [g.replace("_", " ") for g, ok in v["gates"].items() if not ok]
        out.append(f"* **{name}:** {', '.join(failed) if failed else 'none'}")
    out += ["", "## Each year (grid net return by calendar year)", "",
            "| Configuration | " + " | ".join(hm["by_year"]) + " |",
            "|---|" + "---|" * len(hm["by_year"])]
    for name, v in rep["configs"].items():
        out.append(f"| {name} | " + " | ".join(pct(v["metrics"]["by_year"].get(y))
                                               for y in hm["by_year"]) + " |")
    out.append("| Holding | " + " | ".join(pct(x) for x in hm["by_year"].values()) + " |")
    out += ["", "Historical simulation only: no orders were placed, and past results do not "
                "guarantee future results.", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--cache", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--cache", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--commit", default="")
    a = ap.parse_args()
    if a.cmd == "download":
        print(json.dumps(download(a.cache)))
        return
    rep = run(a.cache, a.workers)
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               kind="historical backtest (no orders placed)", paper=False,
               preregistration=PREREG, coins=list(COINS), period=[FIRST, LAST])
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    a.out.with_suffix(".md").write_text(render(rep))
    print(json.dumps({k: {"passed": v["passed"], "net": v["metrics"]["net_return"],
                          "failed": [g for g, ok in v["gates"].items() if not ok]}
                      for k, v in rep["configs"].items()}))


if __name__ == "__main__":
    main()
