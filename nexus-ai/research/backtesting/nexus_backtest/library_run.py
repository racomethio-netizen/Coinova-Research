"""Single pre-registered run of the global strategy library
(docs/research/2026-11-23-global-library-preregistration.md).

    python -m nexus_backtest.library_run --archive15 DIR --archive1d DIR --out FILE
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies.library_global import HIGHER, LIBRARY, resample, signals

from nexus_backtest.crypto_trends import load_archive_dir
from nexus_backtest.library_engine import (
    LibResult,
    Rules,
    metrics,
    per_symbol_pf,
    returns_array,
    run,
    split,
)
from nexus_backtest.significance import (
    holm,
    sqn,
    stationary_bootstrap_pvalue,
    trade_shuffle_drawdowns,
)
from nexus_backtest.spot_engine_run import SYMBOLS, load_15m, rules_from

TS = pd.Timestamp
SPLITS = {
    "intraday": {"train": (TS("2023-01-01", tz="UTC"), TS("2024-07-01", tz="UTC")),
                 "validation": (TS("2024-07-01", tz="UTC"), TS("2025-07-01", tz="UTC")),
                 "test": (TS("2025-07-01", tz="UTC"), TS("2026-11-22", tz="UTC"))},
    "daily": {"train": (TS("2019-01-01", tz="UTC"), TS("2022-07-01", tz="UTC")),
              "validation": (TS("2022-07-01", tz="UTC"), TS("2024-07-01", tz="UTC")),
              "test": (TS("2024-07-01", tz="UTC"), TS("2026-11-22", tz="UTC"))},
}
MIN_TRADES = {"1h": 100, "4h": 100, "1d": 30}


def _pf(m: dict[str, Any]) -> float:
    pf = m.get("profit_factor")
    if pf is None:
        return float("inf") if m.get("trades", 0) and (m.get("net_profit") or 0) > 0 else 0.0
    return float(pf)


def evaluate(code: str, tf: str, base: dict[str, pd.DataFrame], higher: dict[str, pd.DataFrame],
             rules: dict[str, Rules]) -> tuple[dict[str, Any], LibResult]:
    kind = "daily" if tf == "1d" else "intraday"
    sp = SPLITS[kind]
    start, end = sp["train"][0], sp["test"][1]
    sigs = {s: signals(code, base[s], tf, higher[s]) for s in base}
    res = run(sigs, base, tf, start=start, end=end, rules=rules)
    out: dict[str, Any] = {"full": metrics(res)}
    for name, (a, b) in sp.items():
        out[name] = metrics(split(res, a, b))
    rets = returns_array(res.trades)
    out["p_value"] = stationary_bootstrap_pvalue(rets, simulations=5000, block=10.0)
    out["sqn"] = sqn(rets)
    out["mc_drawdown"] = trade_shuffle_drawdowns(res.trades.pnl.to_numpy(), 10_000.0) \
        if len(res.trades) else None
    out["per_symbol_pf"] = per_symbol_pf(res.trades) if len(res.trades) else {}
    counts = res.trades.groupby("symbol").size().to_dict() if len(res.trades) else {}
    out["per_symbol_trades"] = {str(k): int(v) for k, v in counts.items()}
    out["rejections"] = res.rejected
    # Equal-weight buy and hold of the same symbols over the same window (benchmark).
    closes = pd.DataFrame({s: b["close"] for s, b in base.items()})
    closes = closes[(closes.index >= start) & (closes.index < end)].dropna(how="all")
    rets_bh = closes.ffill().pct_change().mean(axis=1).fillna(0.0)
    bh = (1 + rets_bh).cumprod()
    out["buy_and_hold"] = {"return_pct": float(bh.iloc[-1] - 1),
                           "max_drawdown": float((1 - bh / bh.cummax()).max())}
    return out, res


def gates(r: dict[str, Any], tf: str, significant: bool) -> dict[str, bool]:
    f, v, t = r["full"], r["validation"], r["test"]
    mc = r.get("mc_drawdown") or {}
    g = {
        "trades": f.get("trades", 0) >= MIN_TRADES[tf],
        "full_pf_ge_1.20": _pf(f) >= 1.20 and (f.get("expectancy") or 0) > 0,
        "test_pf_ge_1.10": _pf(t) >= 1.10 and (t.get("expectancy") or 0) > 0,
        "validation_pf_ge_1.00": _pf(v) >= 1.00,
        "max_drawdown_le_15pct": f.get("max_drawdown", 1.0) <= 0.15,
        "mc_p95_drawdown_le_20pct": mc.get("p95", 1.0) <= 0.20,
        "holm_significant": significant,
    }
    if tf != "1d":
        eligible = {s: pf for s, pf in r["per_symbol_pf"].items()
                    if r["per_symbol_trades"].get(s, 0) >= 5}
        good = sum(1 for pf in eligible.values() if pf is None or pf > 1)
        g["breadth_half_symbols"] = bool(eligible) and good >= len(eligible) / 2
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive15", required=True)
    ap.add_argument("--archive1d", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d15 = Path(a.archive15)
    m15 = {s: load_15m(d15, s)[0] for s in SYMBOLS}
    rules_raw = rules_from(d15 / "exchangeinfo.json")
    rules = {s: Rules(r.step, r.min_notional) for s, r in rules_raw.items()}
    frames = {tf: {s: resample(df, tf) for s, df in m15.items()} for tf in ("1h", "4h", "1d")}
    daily = {s: load_archive_dir(Path(a.archive1d), s) for s in ("BTCUSDT", "ETHUSDT")}
    weekly = {s: resample(df, "1w") for s, df in daily.items()}
    universes = {"1h": (frames["1h"], frames["4h"]), "4h": (frames["4h"], frames["1d"]),
                 "1d": (daily, weekly)}
    results: dict[str, Any] = {"data": {
        "intraday": {s: [str(df.index[0]), str(df.index[-1]), len(df)] for s, df in m15.items()},
        "daily": {s: [str(df.index[0]), str(df.index[-1]), len(df)] for s, df in daily.items()}},
        "tests": {}}
    for tf, (base, higher) in universes.items():
        assert all(HIGHER[tf] for _ in base)
        for code in LIBRARY:
            r, _ = evaluate(code, tf, base, higher, rules)
            results["tests"][f"{code}@{tf}"] = r
            f = r["full"]
            print(f"{code:28s} {tf:3s} trades={f.get('trades', 0):4d} "
                  f"pf={f.get('profit_factor')} ret={f.get('return_pct')} "
                  f"p={r['p_value']}", flush=True)
    sig = holm({k: v["p_value"] for k, v in results["tests"].items()})
    for k, v in results["tests"].items():
        tf = k.split("@")[1]
        v["holm_significant"] = sig[k]
        v["gates"] = gates(v, tf, sig[k])
        v["passed"] = all(v["gates"].values())
    results["passed"] = sorted(k for k, v in results["tests"].items() if v["passed"])
    Path(a.out).write_text(json.dumps(results, indent=1, default=_json))
    print("PASSED:", results["passed"] or "none")


def _json(x: Any) -> Any:
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return str(x)


if __name__ == "__main__":
    main()
