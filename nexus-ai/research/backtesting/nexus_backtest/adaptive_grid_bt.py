"""Backtest of the adaptive grid system (pre-registration:
docs/research/2026-11-26-adaptive-grid-preregistration.md).

It drives the SAME engine as paper trading (``nexus_strategies.adaptive_grid``) and the
same regime classifier (``nexus_strategies.grid_regime``). Only the order book is
replaced by historical 15m bars:
* each bar becomes four snapshots (open, low, high, close, or open, high, low, close
  when the bar closed down) with a 1 bp spread;
* displayed quantity is a quarter of the bar's volume per snapshot (the engine fills at
  most 25% of it);
* orders armed at a bar's close fill from the next bar on;
* the regime of the last CLOSED hour applies.

Usage: ``python -m nexus_backtest.adaptive_grid_bt --spot-dir D --futures-dir D
--exinfo F --out F``.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies import adaptive_grid as ag
from nexus_strategies import grid_regime as gr

D = Decimal
SPOT = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "LTC")
FUTURES = ("BTCUSDT", "ETHUSDT")
START, END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC")
SETS = [(D(s), D(r)) for s in ("0.4", "0.6", "0.9") for r in ("6", "8", "12")]
DEFAULT = (D("0.6"), D("8"))
SPOT_ALLOC, FUT_ALLOC = D("1000"), D("2000")
TOTAL_EQUITY = SPOT_ALLOC * len(SPOT) + FUT_ALLOC * len(FUTURES)
STOP_COOLDOWN = pd.Timedelta(days=7)
# Futures exchange filters (Binance USD-M, as documented; the spot filters come from the
# saved exchangeInfo).
FUT_FILTERS = {"BTCUSDT": ag.Filters(D("0.1"), D("0.001"), D("0.001"), D("100")),
               "ETHUSDT": ag.Filters(D("0.01"), D("0.001"), D("0.001"), D("20"))}


# ------------------------------------------------------------------------------- data
def load_spot(spot_dir: Path, base: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    m5 = pd.read_csv(spot_dir / f"{base}_5m.csv.gz", parse_dates=["date"]).set_index("date")
    h1 = pd.read_csv(spot_dir / f"{base}_1h.csv.gz", parse_dates=["date"]).set_index("date")
    m15 = m5.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna(subset=["open"])
    return m15, h1


def _zip_csv(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        raw = zf.read(zf.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(df.iloc[0, 0]).isdigit():          # header row present
        df = pd.read_csv(io.BytesIO(raw))
    return df


def load_futures(fdir: Path, sym: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    kl, fr = [], []
    for z in sorted(fdir.glob(f"{sym}-15m-*.zip")):
        df = _zip_csv(z)
        df = df.iloc[:, :6]
        df.columns = ["t", "open", "high", "low", "close", "volume"]
        kl.append(df)
    for z in sorted(fdir.glob(f"{sym}-fundingRate-*.zip")):
        df = _zip_csv(z)
        df = df.iloc[:, [0, 2]]
        df.columns = ["t", "rate"]
        fr.append(df)
    k = pd.concat(kl).astype(float)
    k["date"] = pd.to_datetime(k["t"].astype("int64"), unit="ms", utc=True)
    m15 = k.drop(columns="t").set_index("date").sort_index()
    m15 = m15[~m15.index.duplicated()]
    h1 = m15.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna(subset=["open"])
    f = pd.concat(fr)
    f["date"] = pd.to_datetime(f["t"].astype("int64") // 1000 * 1000, unit="ms", utc=True)
    funding = f.set_index("date")["rate"].astype(float).sort_index()
    return m15, h1, funding


def spot_filters(exinfo: Path) -> dict[str, ag.Filters]:
    info = json.loads(exinfo.read_text())
    out = {}
    for s in info["symbols"]:
        f: dict[str, Any] = {x["filterType"]: x for x in s["filters"]}
        notional: dict[str, Any] = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        out[s["baseAsset"]] = ag.Filters(
            D(f["PRICE_FILTER"]["tickSize"]).normalize(),
            D(f["LOT_SIZE"]["stepSize"]).normalize(), D(f["LOT_SIZE"]["minQty"]).normalize(),
            D(notional["minNotional"]).normalize())
    return out


# --------------------------------------------------------------------------- simulate
def run_symbol(job: dict[str, Any]) -> dict[str, Any]:
    market, sym = job["market"], job["symbol"]
    m15, h1, funding = job["m15"], job["h1"], job.get("funding")
    spacing, rng = job["params"]
    fee_mult = D(str(job.get("fee_mult", 1)))
    alloc, filters = job["alloc"], job["filters"]
    params = replace(ag.GridParams(), spacing_atr=spacing, range_atr=rng)
    lab = gr.classify(h1)
    lab.index = lab.index + pd.Timedelta(hours=1)           # usable once the hour closed
    reg = lab.reindex(m15.index, method="ffill")
    m15 = m15[(m15.index >= START) & (m15.index < END)]
    reg = reg.loc[m15.index]
    fund = funding[(funding.index >= START) & (funding.index < END)] if funding is not None \
        else None
    fund_iter = iter(fund.items()) if fund is not None else iter(())
    next_fund = next(fund_iter, None)
    cash, g = alloc, None
    stopped_until = START
    trips: list[dict[str, Any]] = []
    entry_time: dict[tuple[int, str], pd.Timestamp] = {}
    fees_total, funding_total = ZERO, ZERO
    daily: dict[str, float] = {}
    exposure_bars = 0
    grids = stops = 0
    o, h, low, c, v = (m15[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    idx = m15.index
    regimes = reg["regime"].to_numpy()
    atrs, rsis = reg["atr_pct"].to_numpy(), reg["rsi"].to_numpy()
    sup, res = reg["support"].to_numpy(), reg["resistance"].to_numpy()
    for i in range(len(idx)):
        t = idx[i]
        regime = str(regimes[i])
        mode = gr.grid_mode(regime, market)
        # Funding settlements that fall inside this bar (actual rates).
        while next_fund is not None and next_fund[0] < t + pd.Timedelta(minutes=15):
            if g is not None and next_fund[0] >= t:
                funding_total += ag.apply_funding(g, D(str(next_fund[1])), D(str(c[i])))
            next_fund = next(fund_iter, None)
        if g is None and mode and t >= stopped_until and not math.isnan(atrs[i]) and \
                30 <= rsis[i] <= 70:
            st, _ = ag.design(ag.DesignInput(
                market, sym, mode, D(str(o[i])), D(str(round(atrs[i], 8))),
                D(str(sup[i])) if not math.isnan(sup[i]) else None,
                D(str(res[i])) if not math.isnan(res[i]) else None, min(alloc, cash),
                TOTAL_EQUITY, filters, t.timestamp()), params)
            if st is not None:
                st.fees = {k: x * fee_mult for k, x in st.fees.items()}
                cash -= st.allocation
                g, grids = st, grids + 1
        if g is not None:
            up = c[i] >= o[i]
            path = (o[i], low[i], h[i], c[i]) if up else (o[i], h[i], low[i], c[i])
            disp = D(str(v[i] / 4))
            for p in path:
                px = D(str(p))
                fills = ag.step(g, ag.Book(px, px * D("1.0001"), disp, disp), t.timestamp())
                for f in fills:
                    fee = D(f["fee"])
                    fees_total += fee
                    key = (f["slot"], g.grid_id)
                    if f["kind"] == "entry":
                        entry_time.setdefault(key, t)
                    elif f["pnl"] is not None:
                        q_, pr = D(f["qty"]), D(f["price"])
                        slot = next(s for s in g.slots if s.idx == f["slot"])
                        entry_fee = q_ * slot.entry_price * g.fees["maker"]
                        trips.append({"exit": t, "pnl": float(D(f["pnl"]) - fee - entry_fee),
                                      "duration_h": (t - entry_time.get(key, t))
                                      .total_seconds() / 3600, "regime": regime,
                                      "stop": bool(f.get("reason")), "notional":
                                      float(q_ * pr)})
                        if slot.held == 0:
                            entry_time.pop(key, None)
                if g.status == "STOPPED":
                    break
            if g.long_qty or g.short_qty:
                exposure_bars += 1
            if g.status == "STOPPED":
                stops += 1
                cash += g.quote
                g, stopped_until = None, t + STOP_COOLDOWN
            else:
                if mode != g.mode and g.status == "RUNNING":
                    ag.disarm(g)
                    g.status = "SUSPENDED"
                elif mode == g.mode and g.status == "SUSPENDED" and 30 <= rsis[i] <= 70:
                    g.status = "RUNNING"
                if ag.should_rebuild(g, t.timestamp(), mode) and \
                        (g.status != "RUNNING" or g.out_of_range_since):
                    cash += g.quote
                    g = None
                elif g.status == "RUNNING":
                    px = D(str(c[i]))
                    ag.arm(g, ag.Book(px, px * D("1.0001"), D(0), D(0)))
        eq = cash + (g.equity(D(str(c[i])), D(str(c[i]))) if g is not None else ZERO)
        daily[t.strftime("%Y-%m-%d")] = float(eq)
    return {"market": market, "symbol": sym, "params": [str(spacing), str(rng)],
            "fee_mult": str(fee_mult), "alloc": float(alloc), "daily": daily,
            "trips": [{**x, "exit": x["exit"].isoformat()} for x in trips],
            "fees": float(fees_total), "funding": float(funding_total),
            "exposure": exposure_bars / max(len(idx), 1), "grids": grids, "stops": stops}


ZERO = D(0)


# ---------------------------------------------------------------------------- report
def curve(res: dict[str, Any]) -> pd.Series:
    s = pd.Series(res["daily"], dtype=float)
    s.index = pd.to_datetime(s.index, utc=True)
    return s


def metrics(equity: pd.Series, trips: list[dict[str, Any]], start_eq: float) -> dict[str, Any]:
    r = equity.pct_change().dropna()
    dd = float((equity / equity.cummax() - 1).min()) if len(equity) else 0.0
    pnl = np.array([t["pnl"] for t in trips]) if trips else np.array([])
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    years = {}
    for y in ("2024", "2025", "2026"):
        e = equity[equity.index.year == int(y)]
        if len(e):
            prev = equity[equity.index < e.index[0]]
            base = prev.iloc[-1] if len(prev) else start_eq
            years[y] = float(e.iloc[-1] / base - 1)
    sd, neg = float(r.std()), r[r < 0]
    return {"net_return": float(equity.iloc[-1] / start_eq - 1) if len(equity) else 0.0,
            "max_drawdown": dd, "trips": len(trips),
            "profit_factor": float(wins / losses) if losses > 0 else None,
            "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
            "avg_duration_h": float(np.mean([t["duration_h"] for t in trips])) if trips
            else None,
            "stops": sum(1 for t in trips if t["stop"]), "by_year": years,
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if len(r) > 90 and sd > 0
            else None,
            "sortino": float(r.mean() / math.sqrt((neg ** 2).mean()) * math.sqrt(365))
            if len(r) > 90 and len(neg) else None}


def pooled(results: list[dict[str, Any]]) -> tuple[pd.Series, list[dict[str, Any]], float]:
    curves = [curve(x) for x in results]
    eq = pd.concat(curves, axis=1).ffill().fillna(pd.Series(
        [x["alloc"] for x in results], index=range(len(results))).to_dict()).sum(axis=1)
    return eq, [t for x in results for t in x["trips"]], sum(x["alloc"] for x in results)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot-dir", type=Path, required=True)
    ap.add_argument("--futures-dir", type=Path, required=True)
    ap.add_argument("--exinfo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    sf = spot_filters(a.exinfo)
    data: dict[tuple[str, str], tuple[Any, ...]] = {}
    for b in SPOT:
        m15, h1 = load_spot(a.spot_dir, b)
        data[("spot", f"{b}USDT")] = (m15, h1, None, SPOT_ALLOC, sf[b])
    for s in FUTURES:
        m15, h1, f = load_futures(a.futures_dir, s)
        data[("futures", s)] = (m15, h1, f, FUT_ALLOC, FUT_FILTERS[s])
    jobs = []
    for (market, sym), (m15, h1, f, alloc, flt) in data.items():
        for ps in SETS:
            jobs.append({"market": market, "symbol": sym, "m15": m15, "h1": h1,
                         "funding": f, "params": ps, "alloc": alloc, "filters": flt})
        jobs.append({"market": market, "symbol": sym, "m15": m15, "h1": h1, "funding": f,
                     "params": DEFAULT, "alloc": alloc, "filters": flt, "fee_mult": 2})
    with ProcessPoolExecutor(a.workers) as ex:
        results = list(ex.map(run_symbol, jobs))
    a.out.write_text(json.dumps({"results": results}, default=str))
    print(f"{len(results)} runs written to {a.out}")


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------- walk-forward report
QUARTERS = [pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC") for y in (2024, 2025, 2026)
            for m in (1, 4, 7, 10)] + [END]


def _window_return(eq: pd.Series, a: pd.Timestamp, b: pd.Timestamp, alloc: float) -> float:
    before = eq[eq.index < a]
    start = before.iloc[-1] if len(before) else alloc
    inside = eq[(eq.index >= a) & (eq.index < b)]
    return float(inside.iloc[-1] / start - 1) if len(inside) else 0.0


def walk_forward(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per test quarter (2024-Q3 on), pick the set with the best pooled median return over
    the previous two quarters, then stitch that set's daily returns per symbol."""
    base = [r for r in results if r["fee_mult"] == "1"]
    by_set: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in base:
        by_set.setdefault(tuple(r["params"]), []).append(r)
    syms = sorted({(r["market"], r["symbol"]) for r in base})
    choice: list[dict[str, Any]] = []
    daily_ret: dict[tuple[str, str], list[pd.Series]] = {s: [] for s in syms}
    oos_trips: list[dict[str, Any]] = []
    for qi in range(2, len(QUARTERS) - 1):
        a, b = QUARTERS[qi], QUARTERS[qi + 1]
        tr0, tr1 = QUARTERS[qi - 2], a
        scores = {ps: float(np.median([_window_return(curve(r), tr0, tr1, r["alloc"])
                                       for r in rs])) for ps, rs in by_set.items()}
        best = max(scores, key=lambda k: scores[k])
        choice.append({"test_quarter": a.strftime("%Y-%m"), "set": list(best),
                       "train_median": scores[best]})
        for r in by_set[best]:
            eq = curve(r)
            seg = eq[(eq.index >= a - pd.Timedelta(days=1)) & (eq.index < b)].pct_change()
            seg = seg[seg.index >= a]
            daily_ret[(r["market"], r["symbol"])].append(seg)
            oos_trips += [t for t in r["trips"] if a <= pd.Timestamp(t["exit"]) < b]
    allocs = {(r["market"], r["symbol"]): r["alloc"] for r in base}
    eq_sym = {s: allocs[s] * (1 + pd.concat(v).fillna(0)).cumprod() for s, v in
              daily_ret.items() if v}
    pooled_eq = pd.concat(eq_sym, axis=1).ffill().sum(axis=1)
    return {"choice": choice, "pooled": pooled_eq, "by_symbol": eq_sym, "trips": oos_trips,
            "start_equity": float(sum(allocs[s] for s in eq_sym))}


def report(raw: dict[str, Any], spot_dir: Path) -> dict[str, Any]:
    from nexus_backtest.market_conditions import by_condition, label_days

    results = raw["results"]
    wf = walk_forward(results)
    pooled_eq, start = wf["pooled"], wf["start_equity"]
    oos = metrics(pooled_eq, wf["trips"], start)
    syms = {}
    for s, eq in wf["by_symbol"].items():
        syms[f"{s[0]}:{s[1]}"] = {"net_return": float(eq.iloc[-1] / eq.iloc[0] - 1)
                                  if len(eq) else 0.0,
                                  "max_drawdown": float((eq / eq.cummax() - 1).min())}
    best_sym = max(syms, key=lambda k: syms[k]["net_return"])
    without = pd.concat({k: v for k, v in wf["by_symbol"].items()
                         if f"{k[0]}:{k[1]}" != best_sym}, axis=1).ffill().sum(axis=1)
    wo_ret = float(without.iloc[-1] / without.iloc[0] - 1)
    stress = [r for r in results if r["fee_mult"] == "2"]
    s_eq, _, s_start = pooled(stress)
    stress_ret = float(s_eq.iloc[-1] / s_start - 1)
    dflt = [r for r in results if r["fee_mult"] == "1" and tuple(r["params"]) ==
            (str(DEFAULT[0]), str(DEFAULT[1]))]
    d_eq, d_trips, d_start = pooled(dflt)
    sens = {}
    for ps in SETS:
        rs = [r for r in results if r["fee_mult"] == "1" and r["params"] == [str(ps[0]),
                                                                            str(ps[1])]]
        e, tr, st0 = pooled(rs)
        sens[f"spacing {ps[0]} x ATR, range {ps[1]} x ATR"] = metrics(e, tr, st0)
    years = oos["by_year"]
    h2_2024 = _window_return(pooled_eq, pd.Timestamp("2024-07-01", tz="UTC"),
                             pd.Timestamp("2025-01-01", tz="UTC"), start)
    yr = {"2024H2": h2_2024, "2025": years.get("2025", 0.0), "2026": years.get("2026", 0.0)}
    gates = {
        "net_return_positive": oos["net_return"] > 0,
        "profit_factor_ge_1_1": (oos["profit_factor"] or 0) >= 1.1,
        "max_drawdown_le_15pct": oos["max_drawdown"] >= -0.15,
        "positive_2_of_3_years": sum(1 for v in yr.values() if v > 0) >= 2,
        "positive_at_2x_fees": stress_ret > 0,
        "positive_without_best_symbol": wo_ret > 0}
    by_regime: dict[str, dict[str, float]] = {}
    for t in wf["trips"]:
        g = by_regime.setdefault(t["regime"], {"trips": 0, "pnl": 0.0, "stops": 0})
        g["trips"] += 1
        g["pnl"] += t["pnl"]
        g["stops"] += int(t["stop"])
    btc = pd.read_csv(spot_dir / "BTC_1h.csv.gz", parse_dates=["date"]).set_index("date")
    btc_d = btc.resample("1D").agg({"close": "last", "volume": "sum"})
    cond = by_condition(pooled_eq.pct_change().dropna(), label_days(btc_d["close"],
                                                                    btc_d["volume"]))
    bench = {}
    for b in SPOT:
        h = pd.read_csv(spot_dir / f"{b}_1h.csv.gz", parse_dates=["date"]).set_index("date")
        h = h[(h.index >= START) & (h.index < END)]
        bench[b] = float(h["close"].iloc[-1] / h["open"].iloc[0] - 1)
    fees = sum(r["fees"] for r in results if r["fee_mult"] == "1" and tuple(r["params"]) ==
               (str(DEFAULT[0]), str(DEFAULT[1])))
    funding = sum(r["funding"] for r in dflt)
    return {"oos_walk_forward": {**oos, "years": yr, "start_equity": start,
                                 "final_equity": float(pooled_eq.iloc[-1])},
            "gates": gates, "passed": all(gates.values()),
            "parameter_choice": wf["choice"], "by_symbol_oos": syms,
            "best_symbol": best_sym, "without_best_symbol_return": wo_ret,
            "default_set_full_window": {**metrics(d_eq, d_trips, d_start),
                                        "fees": fees, "funding": funding,
                                        "per_symbol": {f"{r['market']}:{r['symbol']}": {
                                            "net_return": float(curve(r).iloc[-1] /
                                                                r["alloc"] - 1),
                                            "grids": r["grids"], "stops": r["stops"],
                                            "exposure": r["exposure"]} for r in dflt}},
            "stress_2x_fees_default_set": {"net_return": stress_ret},
            "sensitivity_full_window": sens, "by_regime_oos": by_regime,
            "by_market_condition_oos": cond, "buy_and_hold": bench}


def report_main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--spot-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rep = report(json.loads(a.raw.read_text()), a.spot_dir)
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"gates": rep["gates"], "passed": rep["passed"],
                      "oos": rep["oos_walk_forward"]}, indent=1, default=str))
