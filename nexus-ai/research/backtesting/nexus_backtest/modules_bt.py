"""Historical test of the six strategy modules (unified brief, Phase 3).

Pre-registration: docs/research/2026-11-27-strategy-modules-preregistration.md. Rules, costs,
periods and gates are fixed there before any run on market data.

* Forex (modules 1-3): Exness Standard-account M5 BID bars with the mean spread of each
  bar, built from Exness's public tick archive (``exness_ticks``). Swap from the FRED rate
  differential less a 0.5% a year broker markup, at the 21:00 UTC rollover (Wednesday x3).
* Crypto (modules 4-5): Binance spot 5m klines from data.binance.vision, every file checked
  against Binance's SHA-256. Fee 0.10% per side, slippage 0.05% on market fills.
* Module 6 (Catching Crypto Trends) re-runs its own unchanged harness on daily archives.

Every signal is simulated with ``nexus_strategies.modules.sim`` (the same code as the
forward record). The book allows one position per symbol and three at a time per module,
each risking 0.25% of equity.

Usage::

    python -m nexus_backtest.modules_bt --fx-dir DIR --fred DIR --crypto-5m DIR \\
        --crypto-1d DIR --out results.json [--workers 4]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_config.settings import TIMEFRAME_SECONDS
from nexus_strategies import modules as mods
from nexus_strategies.modules import sim

from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

PREREG = "docs/research/2026-11-27-strategy-modules-preregistration.md"
NEW = ("fx_scalp_pullback", "fx_intraday_breakout", "fx_swing_trend", "crypto_scalp_volume")
FX_START = pd.Timestamp("2016-01-01", tz="UTC")       # Aug-Dec 2015: indicator warm-up only
CRYPTO_START = pd.Timestamp("2021-03-01", tz="UTC")    # Jan-Feb 2021: warm-up only
CRYPTO_MONTHS = ("2021-01", "2026-10")
FX_SLIPPAGE_PIPS = 0.1
SWAP_MARKUP = 0.005                                    # a year, charged on both sides
CRYPTO_FEE, CRYPTO_SLIPPAGE = 0.001, 0.0005
RISK = 0.0025
MAX_OPEN = 3
MIN_TRADES = {"fx_scalp_pullback": 200, "fx_intraday_breakout": 200, "fx_swing_trend": 60,
              "crypto_scalp_volume": 200, "crypto_breakout_retest": 200}
MIN_PF = 1.15
MAX_DD = 0.10
BOOT_SIMS, BOOT_BLOCK = 5000, 10.0


def pip(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


# ------------------------------------------------------------------------------ data


def load_fx(directory: Path, pair: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """M5 BID bars with ``spread`` (the bar's mean spread). Invalid rows are dropped and
    counted, never repaired."""
    df = pd.read_csv(directory / f"{pair}_M5.csv")
    df.index = pd.to_datetime(df["time"], utc=True)
    df = df.rename(columns={"spread_mean": "spread"})
    df = df[["open", "high", "low", "close", "spread"]].astype(float).sort_index()
    n0 = len(df)
    df = df[~df.index.duplicated()]
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) \
        | (df["low"] > df[["open", "close"]].min(axis=1)) \
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1) \
        | df.isna().any(axis=1) | (df["spread"] < 0)
    return df[~bad], {"rows": n0, "dropped": int(n0 - len(df) + int(bad.sum())),
                      "kept": int((~bad).sum())}


def months(first: str, last: str) -> list[str]:
    out, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load_crypto(cache: Path, symbol: str, first: str = CRYPTO_MONTHS[0],
                last: str = CRYPTO_MONTHS[1]) -> tuple[pd.DataFrame, dict[str, int]]:
    """5m klines; every monthly file must match Binance's published SHA-256."""
    from nexus_backtest.freqtrade_community import parse_kline_zip

    parts, checked = [], 0
    for mo in months(first, last):
        z = cache / f"{symbol}-5m-{mo}.zip"
        ck = cache / f"{symbol}-5m-{mo}.zip.CHECKSUM"
        data = z.read_bytes()
        if hashlib.sha256(data).hexdigest() != ck.read_text().split()[0]:
            raise ValueError(f"checksum mismatch: {z.name}")
        checked += 1
        parts.append(parse_kline_zip(data))
    df = pd.concat(parts).drop_duplicates("date").sort_values("date").set_index("date")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    expected = int((df.index[-1] - df.index[0]) / pd.Timedelta(minutes=5)) + 1
    return df, {"files_checked": checked, "bars": len(df), "missing_bars": expected - len(df)}


def fx_carry(fred: Path, pair: str) -> sim.Carry | None:
    """Swap at the 21:00 UTC rollover from the FRED rate differential (base - quote) less
    the markup. Where the rate is unknown nothing is charged (and the report says so)."""
    from nexus_strategies.nqs1.engine import rollovers

    from nexus_backtest.nqs1_bt import build_carry

    diff = build_carry(fred, (pair,)).get(pair)
    if diff is None or diff.empty:
        return None
    days = diff.index
    vals = diff.to_numpy(float)

    def cost(entry_ns: int, exit_ns: int, side: int, price: float) -> float:
        a = pd.Timestamp(entry_ns, tz="UTC")
        z = pd.Timestamp(exit_ns, tz="UTC")
        n = rollovers(a.to_pydatetime(), z.to_pydatetime())
        if n == 0:
            return 0.0
        i = int(days.searchsorted(a.normalize(), side="right")) - 1
        if i < 0 or a.normalize() - days[i] > pd.Timedelta(days=4):
            return 0.0
        daily = (side * vals[i] - SWAP_MARKUP) / 365
        return float(-daily * n * price)

    return cost


# ----------------------------------------------------------------------------- trades


@dataclass(frozen=True)
class Job:
    code: str
    symbol: str
    fx_dir: str
    fred: str
    crypto_dir: str
    cost_multiplier: float = 1.0     # 0: the post-run "before costs" diagnostic only


def _frames(spec: mods.ModuleSpec, job: Job) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    if spec.market == "forex":
        m5, info = load_fx(Path(job.fx_dir), job.symbol)
        return mods.fx_frames(m5), info
    m5, info = load_crypto(Path(job.crypto_dir), job.symbol)
    frames = mods.crypto_frames(m5)
    if job.symbol != "BTCUSDT" and job.code == "crypto_breakout_retest":
        btc, _ = load_crypto(Path(job.crypto_dir), "BTCUSDT")
        frames["btc_4h"] = mods.crypto_frames(btc)["4h"]
    return frames, info


def run_job(job: Job) -> dict[str, Any]:
    """Signals and simulated candidate trades of one module on one symbol, at normal and
    2x costs (same signals)."""
    spec = mods.get(job.code)
    frames, info = _frames(spec, job)
    assert spec.signals is not None
    sig = spec.signals(frames, spec.config)
    start = FX_START if spec.market == "forex" else CRYPTO_START
    sig = sig[sig.index >= start]
    exec_df = frames[spec.exec_tf]
    bars = sim.Bars.from_frame(exec_df, TIMEFRAME_SECONDS[spec.exec_tf])
    if spec.market == "forex":
        costs = sim.Costs("forex", slippage=FX_SLIPPAGE_PIPS * pip(job.symbol))
        carry = fx_carry(Path(job.fred), job.symbol)
    else:
        costs = sim.Costs("crypto", slippage=CRYPTO_SLIPPAGE, fee=CRYPTO_FEE)
        carry = None
    delay = int(spec.max_entry_delay.total_seconds() * 1e9)
    period = TIMEFRAME_SECONDS[spec.signal_tf]
    if job.cost_multiplier != 1.0:
        costs = costs.stressed(job.cost_multiplier)
    normal = sim.candidates(bars, sig, period, delay, costs, symbol=job.symbol, carry=carry)
    double = [] if job.cost_multiplier != 1.0 else sim.candidates(
        bars, sig, period, delay, costs.stressed(2), symbol=job.symbol, carry=carry)
    return {"code": job.code, "symbol": job.symbol, "data": info, "signals": len(sig),
            "carry_known": carry is not None,
            "first_bar": str(exec_df.index[0]), "last_bar": str(exec_df.index[-1]),
            "normal": [t.__dict__ for t in normal], "double": [t.__dict__ for t in double]}


# ---------------------------------------------------------------------------- metrics


def equity_drawdown(r: np.ndarray, risk: float = RISK) -> float:
    eq = np.cumprod(1 + risk * r)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return float(np.max(1 - eq / peak)) if len(eq) else 0.0


def metrics(trades: list[sim.Trade], start: pd.Timestamp, end: pd.Timestamp, *,
            p_value: bool = True) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    tr = sorted(trades, key=lambda t: t.entry_time)
    r = np.array([t.net_r for t in tr])
    by_exit = np.array([t.net_r for t in sorted(tr, key=lambda t: t.exit_time)])
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    mid = start + (end - start) / 2
    entry = pd.to_datetime([t.entry_time for t in tr], utc=True)
    halves = {"first": r[entry < mid], "second": r[entry >= mid]}
    years: dict[str, dict[str, float]] = {}
    for y in sorted(set(entry.year)):
        x = r[entry.year == y]
        years[str(y)] = {"trades": len(x), "mean_r": float(x.mean()), "total_r": float(x.sum())}
    syms: dict[str, dict[str, float]] = {}
    for s in sorted({t.symbol for t in tr}):
        x = np.array([t.net_r for t in tr if t.symbol == s])
        syms[s] = {"trades": len(x), "mean_r": float(x.mean()), "win_rate": float((x > 0).mean())}
    reasons: dict[str, int] = {}
    for t in tr:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {
        "trades": len(r), "win_rate": float((r > 0).mean()), "mean_r": float(r.mean()),
        "median_r": float(np.median(r)), "total_r": float(r.sum()),
        "profit_factor": float(pos / neg) if neg > 0 else None,
        "max_drawdown": equity_drawdown(by_exit),
        "return_at_risk": float(np.prod(1 + RISK * by_exit) - 1),
        "half_mean_r": {k: (float(v.mean()) if len(v) else None) for k, v in halves.items()},
        "half_trades": {k: len(v) for k, v in halves.items()},
        "avg_bars": float(np.mean([t.bars for t in tr])),
        "carry_r": float(np.sum([t.carry / t.risk for t in tr])),
        "exit_reasons": reasons, "years": years, "symbols": syms,
        "p_value": stationary_bootstrap_pvalue(r, simulations=BOOT_SIMS, block=BOOT_BLOCK)
        if p_value else None,
    }


def gates(code: str, m: dict[str, Any], m2: dict[str, Any], significant: bool
          ) -> dict[str, bool]:
    halves = m.get("half_mean_r", {})
    return {
        "enough_trades": m.get("trades", 0) >= MIN_TRADES[code],
        "positive_expectancy": (m.get("mean_r") or -1) > 0,
        "significant_after_holm": significant,
        "profit_factor": (m.get("profit_factor") or 0) >= MIN_PF,
        "both_halves_positive": all(v is not None and v > 0 for v in halves.values()),
        "positive_at_2x_costs": (m2.get("mean_r") or -1) > 0,
        "max_drawdown": m.get("max_drawdown", 1) <= MAX_DD,
    }


def _trade(d: dict[str, Any]) -> sim.Trade:
    return sim.Trade(**d)


def evaluate(results: list[dict[str, Any]], end: dict[str, pd.Timestamp]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code in [c for c in mods.MODULES if mods.get(c).signals is not None]:
        spec = mods.get(code)
        rows = [r for r in results if r["code"] == code]
        normal = [_trade(t) for r in rows for t in r["normal"]]
        double = [_trade(t) for r in rows for t in r["double"]]
        taken = sim.book(normal, MAX_OPEN)
        keys = {(t.symbol, t.signal_time) for t in taken}
        taken2 = [t for t in double if (t.symbol, t.signal_time) in keys]
        start = FX_START if spec.market == "forex" else CRYPTO_START
        out[code] = {
            "name": spec.name, "market": spec.market, "style": spec.style,
            "evidence": spec.evidence, "config": spec.config_dict(),
            "symbols": {r["symbol"]: {"signals": r["signals"], "data": r["data"],
                                      "carry_known": r["carry_known"],
                                      "first_bar": r["first_bar"], "last_bar": r["last_bar"]}
                        for r in rows},
            "candidates": len(normal), "skipped_by_book": len(normal) - len(taken),
            "metrics": metrics(taken, start, end[spec.market]),
            "metrics_2x_costs": metrics(taken2, start, end[spec.market], p_value=False),
            "trades_sample": [t.as_dict() for t in taken[:20]],
        }
    pvals = {c: out[c]["metrics"].get("p_value") for c in NEW}
    sig = holm(pvals)
    for code, res in out.items():
        if code in NEW:
            g = gates(code, res["metrics"], res["metrics_2x_costs"], sig[code])
            res["gates"], res["passed"] = g, all(g.values())
        else:
            res["gates"], res["passed"] = None, False
            res["verdict_note"] = mods.get(code).note
    return out


def before_costs(fx_dir: Path, fred: Path, crypto_5m: Path, *, workers: int = 4
                 ) -> dict[str, Any]:
    """POST-RUN DIAGNOSTIC, not a gate: the same rules with every cost at zero (spread,
    slippage, fees and swap), to show whether a module loses before costs or only after.
    Added after the single run; it cannot change any verdict."""
    jobs = [Job(code, sym, str(fx_dir), str(fred), str(crypto_5m), 0.0)
            for code, spec in mods.MODULES.items() if spec.signals is not None
            for sym in spec.symbols]
    with ProcessPoolExecutor(workers, mp_context=get_context("spawn")) as ex:
        results = list(ex.map(run_job, jobs))
    out = {}
    for code in {r["code"] for r in results}:
        spec = mods.get(code)
        taken = sim.book([_trade(t) for r in results if r["code"] == code for t in r["normal"]],
                         MAX_OPEN)
        start = FX_START if spec.market == "forex" else CRYPTO_START
        end = max(pd.Timestamp(r["last_bar"]) for r in results if r["code"] == code)
        m = metrics(taken, start, end, p_value=False)
        out[code] = {k: m.get(k) for k in ("trades", "win_rate", "mean_r", "profit_factor")}
    return out


def crypto_trends_replication(daily_dir: Path) -> dict[str, Any]:
    from nexus_backtest.crypto_trends import SimConfig, load_archive_dir
    from nexus_backtest.crypto_trends_run import evaluate as ct_evaluate

    bars = {s: load_archive_dir(daily_dir, s) for s in ("BTCUSDT", "ETHUSDT")}
    res = ct_evaluate(bars, SimConfig())
    return {"name": mods.get("crypto_swing_trend").name, "evidence": "earlier_pass",
            "note": mods.get("crypto_swing_trend").note, "replication": res,
            "passed": None}


def run(fx_dir: Path, fred: Path, crypto_5m: Path, crypto_1d: Path, *, workers: int = 4
        ) -> dict[str, Any]:
    jobs = [Job(code, sym, str(fx_dir), str(fred), str(crypto_5m))
            for code, spec in mods.MODULES.items() if spec.signals is not None
            for sym in spec.symbols]
    with ProcessPoolExecutor(workers, mp_context=get_context("spawn")) as ex:
        results = list(ex.map(run_job, jobs))
    ends = {"forex": max(pd.Timestamp(r["last_bar"]) for r in results
                         if mods.get(r["code"]).market == "forex"),
            "crypto": max(pd.Timestamp(r["last_bar"]) for r in results
                          if mods.get(r["code"]).market == "crypto")}
    modules = evaluate(results, ends)
    modules["crypto_swing_trend"] = crypto_trends_replication(crypto_1d)
    return {"run_at": datetime.now(UTC).isoformat(), "preregistration": PREREG,
            "periods": {"forex": [str(FX_START), str(ends["forex"])],
                        "crypto": [str(CRYPTO_START), str(ends["crypto"])]},
            "costs": {"fx_slippage_pips": FX_SLIPPAGE_PIPS, "fx_spread": "per-bar mean",
                      "swap_markup": SWAP_MARKUP, "crypto_fee": CRYPTO_FEE,
                      "crypto_slippage": CRYPTO_SLIPPAGE},
            "book": {"risk_per_trade": RISK, "max_open": MAX_OPEN},
            "holm_family": list(NEW), "modules": modules}


def _num(x: Any, nd: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) \
        else f"{x:.{nd}f}"


def render(rep: dict[str, Any]) -> str:
    lines = ["| Module | Trades | Win rate | Mean R (net) | Profit factor | Mean R at 2x costs "
             "| Max drawdown | p-value | Result |", "|---|---|---|---|---|---|---|---|---|"]
    for code, res in rep["modules"].items():
        if code == "crypto_swing_trend":
            continue
        m, m2 = res["metrics"], res["metrics_2x_costs"]
        verdict = "**PASS**" if res["passed"] else (
            "FAIL" if res["gates"] else "not eligible (earlier FAIL stands)")
        n = m.get("trades", 0)
        win = f"{_num(100 * m['win_rate'], 1)}%" if n else "—"
        dd = f"{_num(100 * m['max_drawdown'], 1)}%" if n else "—"
        lines.append(
            f"| {res['name']} | {n} | {win} | {_num(m.get('mean_r'), 3)} "
            f"| {_num(m.get('profit_factor'))} | {_num(m2.get('mean_r'), 3)} | {dd} "
            f"| {_num(m.get('p_value'), 4)} | {verdict} |")
    ct = rep["modules"]["crypto_swing_trend"]["replication"]
    lines += ["", f"Crypto swing trend momentum (replication of the 23 Nov pass): "
              f"{'PASS' if ct.get('passed') else 'FAIL'} — gates {json.dumps(ct.get('gates'))}"]
    for res in rep["modules"].values():
        if res.get("gates"):
            failed = [k for k, v in res["gates"].items() if not v]
            lines.append(f"* {res['name']}: failed gates: {', '.join(failed) or 'none'}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx-dir", type=Path, required=True)
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--crypto-5m", type=Path, required=True)
    ap.add_argument("--crypto-1d", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--before-costs", action="store_true",
                    help="post-run diagnostic only: every cost at zero, no gates")
    a = ap.parse_args()
    if a.before_costs:
        diag = before_costs(a.fx_dir, a.fred, a.crypto_5m, workers=a.workers)
        a.out.write_text(json.dumps({"diagnostic": "before costs (post-run, not a gate)",
                                     "run_at": datetime.now(UTC).isoformat(),
                                     "modules": diag}, indent=1))
        print(json.dumps(diag, indent=1))
        return
    rep = run(a.fx_dir, a.fred, a.crypto_5m, a.crypto_1d, workers=a.workers)
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    a.out.with_suffix(".md").write_text(render(rep) + "\n")
    print(render(rep))


if __name__ == "__main__":
    main()
