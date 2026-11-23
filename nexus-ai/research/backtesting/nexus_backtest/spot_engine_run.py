"""Single pre-registered run (docs/research/2026-11-23-spot-engine-preregistration.md).

``python -m nexus_backtest.spot_engine_run --archive DIR --out FILE``
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from nexus_backtest.spot_engine import EngineConfig, SymbolRules, run, with_params
from nexus_backtest.spot_report import report, trade_stats

SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
           "LINKUSDT", "AVAXUSDT", "LTCUSDT")
EVAL = (pd.Timestamp("2022-11-01", tz="UTC"), pd.Timestamp("2026-11-22", tz="UTC"))
SPLITS = {"train": (EVAL[0], pd.Timestamp("2024-05-01", tz="UTC")),
          "validation": (pd.Timestamp("2024-05-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
          "test": (pd.Timestamp("2025-07-01", tz="UTC"), EVAL[1])}
STRATEGIES = ("trend_pullback", "breakout_retest_v2")
SENSITIVITY = {
    "trend_pullback": {"pullback__vol_mult": (1.0, 1.5), "pullback__ext_atr": (0.75, 1.5),
                       "pullback__rr_min": (1.5, 2.5)},
    "breakout_retest_v2": {"breakout__vol_mult": (1.25, 2.0), "breakout__max_width_atr": (3.0, 5.0),
                           "breakout__retest_window": (4, 8)},
}


def load_15m(directory: Path, symbol: str) -> tuple[pd.DataFrame, int]:
    rows: dict[datetime, tuple[float, ...]] = {}
    for z in sorted(directory.glob(f"{symbol}-15m-*.zip")):
        with zipfile.ZipFile(z) as zf:
            text = zf.read(zf.namelist()[0]).decode()
        for r in csv.reader(io.StringIO(text)):
            if not r or not r[0][:1].isdigit():
                continue
            ts = int(r[0])
            ts = ts // 1000 if ts > 10**14 else ts
            rows[datetime.fromtimestamp(ts / 1000, tz=UTC)] = tuple(float(x) for x in r[1:6])
    df = pd.DataFrame.from_dict(rows, orient="index",
                                columns=["open", "high", "low", "close", "volume"]).sort_index()
    df.index = pd.DatetimeIndex(df.index)
    full = pd.date_range(df.index[0], df.index[-1], freq="15min", tz="UTC")
    return df, len(full) - len(df)          # missing bars stay missing (never filled)


def rules_from(path: Path) -> dict[str, SymbolRules]:
    out = {}
    for s in json.loads(path.read_text())["symbols"]:
        f = {x["filterType"]: x for x in s["filters"]}
        notional = f.get("NOTIONAL") or f["MIN_NOTIONAL"]
        out[s["symbol"]] = SymbolRules(float(f["PRICE_FILTER"]["tickSize"]),
                                       float(f["LOT_SIZE"]["stepSize"]),
                                       float(notional["minNotional"]))
    return out


def _slice(tr: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    if tr.empty:
        return tr
    t = pd.to_datetime(tr.entry_time)
    return tr[(t >= lo) & (t < hi)]


def pf(d: dict[str, Any]) -> float:
    return float(d.get("profit_factor") or 0.0)


def gates(res_report: dict[str, Any], tr: pd.DataFrame) -> dict[str, Any]:
    split = {k: trade_stats(_slice(tr, *v)) for k, v in SPLITS.items()}
    sym = tr.groupby("symbol").pnl.agg(["count", "sum"]) if not tr.empty else pd.DataFrame()
    by_sym = {s: trade_stats(g) for s, g in tr.groupby("symbol")} if not tr.empty else {}
    eligible = [s for s, d in by_sym.items() if d["trades"] >= 10]
    share_pf1 = (sum(1 for s in eligible if pf(by_sym[s]) > 1) / len(eligible)) if eligible else 0
    best = sym["sum"].idxmax() if len(sym) else None
    ex_best = trade_stats(tr[tr.symbol != best]) if best is not None else {}
    reg = {str(k): g.pnl.sum() for k, g in tr.groupby("regime") if len(g) >= 10} \
        if not tr.empty else {}
    full = res_report
    g = {
        "full_pf_ge_1.30": pf(full) >= 1.30,
        "full_expectancy_positive": (full.get("expectancy") or 0) > 0,
        "full_max_drawdown_le_10pct": full["max_drawdown"] <= 0.10,
        "full_trades_ge_200": full.get("trades", 0) >= 200,
        "test_pf_ge_1.30": pf(split["test"]) >= 1.30,
        "test_expectancy_positive": (split["test"].get("expectancy") or 0) > 0,
        "no_oos_degradation": pf(split["test"]) >= 0.75 * pf(split["train"]),
        "not_one_coin": share_pf1 >= 0.6 and pf(ex_best) >= 1.10,
        "not_one_regime": sum(1 for v in reg.values() if v > 0) >= 2,
    }
    return {"gates": g, "passed": all(g.values()), "splits": split,
            "symbols_pf_gt_1_share": share_pf1, "best_symbol": best,
            "without_best_symbol": ex_best, "regime_net_pnl_10plus": reg}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d = Path(a.archive)
    data, missing = {}, {}
    for s in SYMBOLS:
        data[s], missing[s] = load_15m(d, s)
    rules = rules_from(d / "exchangeinfo.json")
    out: dict[str, Any] = {"data": {s: [str(v.index[0]), str(v.index[-1]), len(v),
                                        missing[s]] for s, v in data.items()}}
    for strat in STRATEGIES:
        cfg = EngineConfig(strategies=(strat,))
        res = run(data, rules, cfg, start=EVAL[0], end=EVAL[1])
        rep = report(res, data["BTCUSDT"]["close"])
        tr = pd.DataFrame(res.trades)
        out[strat] = {"report": rep, **gates(rep, tr)}
        sens = {}
        for key, values in SENSITIVITY[strat].items():
            for v in values:
                r2 = run(data, rules, with_params(cfg, **{key: v}), start=SPLITS["train"][0],
                         end=SPLITS["train"][1])
                st = trade_stats(pd.DataFrame(r2.trades))
                sens[f"{key}={v}"] = {"trades": st.get("trades"),
                                      "profit_factor": st.get("profit_factor"),
                                      "expectancy": st.get("expectancy")}
        out[strat]["train_sensitivity"] = sens
        Path(a.out).write_text(json.dumps(out, indent=1, default=str))
        print(strat, "PASS" if out[strat]["passed"] else "FAIL", json.dumps(out[strat]["gates"]),
              flush=True)
    both = run(data, rules, EngineConfig(), start=EVAL[0], end=EVAL[1])
    out["combined_engine"] = report(both, data["BTCUSDT"]["close"])
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print("combined done", flush=True)


if __name__ == "__main__":
    main()
