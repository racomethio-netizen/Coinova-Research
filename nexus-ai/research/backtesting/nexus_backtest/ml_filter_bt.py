"""Historical test of the machine-learning FILTER on strategy-module signals (unified
brief Phase 4). Pre-registration: docs/research/2026-11-28-ml-filter-preregistration.md.

For each tested module, every signal of the Phase 3 test is simulated with the same
costs (``modules_bt``), and the market at the signal bar is described by
``nexus_ai.meta_filter.signal_features``. Walk-forward by calendar year:
* the test year's trades are filtered by a ``MetaFilter`` trained only on trades that
  had CLOSED before 1 January of that year;
* the allowed trades of all test years then go through the same book as Phase 3 (one
  position per symbol, three at a time, 0.25% risk) and the same gates.

Usage::

    python -m nexus_backtest.ml_filter_bt --fx-dir DIR --fred DIR --out results.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_ai.drift import psi
from nexus_ai.evaluation import classification_metrics
from nexus_ai.meta_filter import FEATURES, MetaFilter, bar_features, signal_features
from nexus_config.settings import TIMEFRAME_SECONDS
from nexus_strategies import modules as mods
from nexus_strategies.modules import sim

from nexus_backtest import modules_bt as mb
from nexus_backtest.significance import holm

PREREG = "docs/research/2026-11-28-ml-filter-preregistration.md"
TESTED = ("fx_scalp_pullback", "fx_intraday_breakout")
NOT_TESTED = {
    "fx_swing_trend": "933 signals in 11 years; about 250 before the first test year: too "
                      "few to train",
    "crypto_scalp_volume": "288 signals since 2021: too few to train",
    "crypto_breakout_retest": "94 signals since 2021: too few to train",
    "crypto_swing_trend": "a portfolio rule, not a stream of trades to filter",
}
TEST_YEARS = tuple(range(2020, 2027))
MIN_TRAIN = 1500
THRESHOLD = 0.40


def job(args: tuple[str, str, str, str]) -> dict[str, Any]:
    """One module on one pair: every simulated signal with its features, normal and 2x
    cost results."""
    code, symbol, fx_dir, fred = args
    spec = mods.get(code)
    m5, _ = mb.load_fx(Path(fx_dir), symbol)
    frames = mods.fx_frames(m5)
    assert spec.signals is not None
    sig = spec.signals(frames, spec.config)
    sig = sig[sig.index >= mb.FX_START]
    exec_df = frames[spec.exec_tf]
    bars = sim.Bars.from_frame(exec_df, TIMEFRAME_SECONDS[spec.exec_tf])
    costs = sim.Costs("forex", slippage=mb.FX_SLIPPAGE_PIPS * mb.pip(symbol))
    carry = mb.fx_carry(Path(fred), symbol)
    delay = int(spec.max_entry_delay.total_seconds() * 1e9)
    period = TIMEFRAME_SECONDS[spec.signal_tf]
    normal = sim.candidates(bars, sig, period, delay, costs, symbol=symbol, carry=carry)
    double = {t.signal_time: t for t in sim.candidates(bars, sig, period, delay,
                                                        costs.stressed(2), symbol=symbol,
                                                        carry=carry)}
    feats = signal_features(bar_features(frames, spec.signal_tf), sig,
                            frames[spec.signal_tf]["close"],
                            frames[spec.signal_tf].get("spread"),
                            spec.symbols.index(symbol))
    rows = []
    for t in normal:
        ts = pd.Timestamp(t.signal_time, tz="UTC")
        d = double.get(t.signal_time)
        rows.append({"trade": t.__dict__, "double": d.__dict__ if d else None,
                     "features": {k: (None if pd.isna(v) else float(v))
                                  for k, v in feats.loc[ts].items()}})
    return {"code": code, "symbol": symbol, "rows": rows,
            "last_bar": str(exec_df.index[-1])}


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame([r["features"] for r in rows], columns=list(FEATURES)).astype(float)
    df["net_r"] = [r["trade"]["net_r"] for r in rows]
    df["entry"] = pd.to_datetime([r["trade"]["entry_time"] for r in rows], utc=True)
    df["exit"] = pd.to_datetime([r["trade"]["exit_time"] for r in rows], utc=True)
    df["i"] = range(len(rows))
    return df.sort_values("entry").reset_index(drop=True)


def evaluate_module(code: str, rows: list[dict[str, Any]], end: pd.Timestamp
                    ) -> dict[str, Any]:
    df = _frame(rows)
    folds, kept, ys, ps, raws = [], [], [], [], []
    for year in TEST_YEARS:
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        stop = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        train = df[df["exit"] < start]
        test = df[(df["entry"] >= start) & (df["entry"] < stop)]
        if len(test) == 0:
            continue
        if len(train) < MIN_TRAIN:
            folds.append({"year": year, "skipped": f"only {len(train)} training trades"})
            continue
        mf = MetaFilter(threshold=THRESHOLD).fit(train[list(FEATURES)],
                                                 (train["net_r"] > 0).to_numpy(int),
                                                 train["entry"], train["exit"])
        p = mf.probability(test[list(FEATURES)])
        raw = mf.raw(test[list(FEATURES)])
        allow = p >= THRESHOLD
        kept += test["i"][allow].tolist()
        ys.append((test["net_r"] > 0).to_numpy(float))
        ps.append(p)
        raws.append(raw)
        drift = {f: psi(train[f], test[f]) for f in FEATURES}
        vals = [v for v in drift.values() if v is not None]
        folds.append({"year": year, "train": len(train), "test": len(test),
                      "allowed": int(allow.sum()), **mf.info,
                      "psi_mean": float(np.mean(vals)) if vals else None,
                      "psi_max": float(np.max(vals)) if vals else None,
                      "psi_max_feature": max(drift, key=lambda k: drift[k] or -1)})
    start = pd.Timestamp(f"{TEST_YEARS[0]}-01-01", tz="UTC")
    test_all = df[df["entry"] >= start]["i"].tolist()

    def book(idx: list[int], key: str = "trade") -> list[sim.Trade]:
        return sim.book([sim.Trade(**rows[i][key]) for i in idx if rows[i][key]], mb.MAX_OPEN)

    filtered = book(kept)
    keys = {(t.symbol, t.signal_time) for t in filtered}
    filtered_2x = [sim.Trade(**r["double"]) for r in rows if r["double"]
                   and (r["double"]["symbol"], r["double"]["signal_time"]) in keys]
    unfiltered = book(test_all)
    y, p = (np.concatenate(ys), np.concatenate(ps)) if ys else (np.array([]), np.array([]))
    cls = classification_metrics(y, p, float(y.mean()), THRESHOLD) if len(y) else None
    per_model = {}
    if raws:
        r = np.vstack(raws)
        for k, name in enumerate(("lightgbm", "xgboost", "catboost")):
            per_model[name] = classification_metrics(y, r[:, k], float(y.mean()), 0.5)["roc_auc"]
    return {"name": mods.get(code).name, "folds": folds,
            "metrics": mb.metrics(filtered, start, end),
            "metrics_2x_costs": mb.metrics(filtered_2x, start, end, p_value=False),
            "unfiltered_same_years": mb.metrics(unfiltered, start, end, p_value=False),
            "classification": cls, "auc_by_model_raw": per_model,
            "candidates_test_years": len(test_all), "allowed": len(kept)}


def run(fx_dir: Path, fred: Path, *, workers: int = 4) -> dict[str, Any]:
    jobs = [(c, s, str(fx_dir), str(fred)) for c in TESTED for s in mods.get(c).symbols]
    with ProcessPoolExecutor(workers, mp_context=get_context("spawn")) as ex:
        results = list(ex.map(job, jobs))
    end = max(pd.Timestamp(r["last_bar"]) for r in results)
    out: dict[str, Any] = {}
    for code in TESTED:
        rows = [row for r in results if r["code"] == code for row in r["rows"]]
        out[code] = evaluate_module(code, rows, end)
    sig = holm({c: out[c]["metrics"].get("p_value") for c in TESTED})
    for code in TESTED:
        g = mb.gates(code, out[code]["metrics"], out[code]["metrics_2x_costs"], sig[code])
        out[code]["gates"], out[code]["passed"] = g, all(g.values())
    return {"run_at": datetime.now(UTC).isoformat(), "preregistration": PREREG,
            "test_years": [TEST_YEARS[0], TEST_YEARS[-1]], "threshold": THRESHOLD,
            "min_train": MIN_TRAIN, "features": list(FEATURES), "modules": out,
            "not_tested": NOT_TESTED, "holm_family": list(TESTED)}


def render(rep: dict[str, Any]) -> str:
    lines = ["| Module | Allowed / signals | Trades (booked) | Mean R filtered | Mean R "
             "unfiltered | Profit factor | Mean R at 2x | AUC | p-value | Result |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for res in rep["modules"].values():
        m, u, m2 = res["metrics"], res["unfiltered_same_years"], res["metrics_2x_costs"]
        auc = (res["classification"] or {}).get("roc_auc")
        lines.append(
            f"| {res['name']} | {res['allowed']} / {res['candidates_test_years']} "
            f"| {m.get('trades', 0)} | {mb._num(m.get('mean_r'), 3)} "
            f"| {mb._num(u.get('mean_r'), 3)} | {mb._num(m.get('profit_factor'))} "
            f"| {mb._num(m2.get('mean_r'), 3)} | {mb._num(auc, 3)} "
            f"| {mb._num(m.get('p_value'), 4)} | {'**PASS**' if res['passed'] else 'FAIL'} |")
    for res in rep["modules"].values():
        failed = [k for k, v in res["gates"].items() if not v]
        lines.append(f"* {res['name']}: failed gates: {', '.join(failed) or 'none'}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx-dir", type=Path, required=True)
    ap.add_argument("--fred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    rep = run(a.fx_dir, a.fred, workers=a.workers)
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    a.out.with_suffix(".md").write_text(render(rep) + "\n")
    print(render(rep))


if __name__ == "__main__":
    main()
