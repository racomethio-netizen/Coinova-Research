"""Batch 3 tests (pre-registration: docs/research/2026-11-26-batch3-preregistration.md).

1. Grid v2 (close inventory on a regime break) vs v1 on UNSEEN 2020-07..2023-12 spot data,
   through ``adaptive_grid_bt.run_symbol`` (same engine and regime).
2. Volatility-targeted holding (Moreira & Muir) vs holding, BTC / ETH / 50-50, 2020-07..
   2026-10.
3. BTC/ETH pairs trade on perpetuals (``nexus_strategies.stat_arb``) with actual funding,
   2024-01..2026-10.

Usage: ``python -m nexus_backtest.batch3 --spot15 D --spot1h D --futures D --exinfo F
--out F --part grid|vol|pairs``.
"""

from __future__ import annotations

import argparse
import hashlib
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
from nexus_strategies import stat_arb

from nexus_backtest import adaptive_grid_bt as bt
from nexus_backtest.significance import stationary_bootstrap_pvalue

GRID_START = pd.Timestamp("2020-07-01", tz="UTC")
GRID_END = pd.Timestamp("2024-01-01", tz="UTC")
VOL_START, VOL_END = pd.Timestamp("2020-07-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC")
VOL_HALF = pd.Timestamp("2023-07-01", tz="UTC")
PAIR_START, PAIR_END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-11-01",
                                                                            tz="UTC")
COST = 0.0015


# ------------------------------------------------------------------------------ data
def load_spot15(d: Path, base: str) -> pd.DataFrame:
    parts = []
    for z in sorted(d.glob(f"{base}USDT-15m-*.zip")):
        with zipfile.ZipFile(z) as zf:
            raw = zf.read(zf.namelist()[0])
        df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6))
        df = df[pd.to_numeric(df[0], errors="coerce").notna()].astype(float)
        t = np.where(df[0] > 1e14, df[0] // 1000, df[0]).astype("int64")
        df.index = pd.to_datetime(t, unit="ms", utc=True)
        parts.append(df.iloc[:, 1:6].set_axis(["open", "high", "low", "close", "volume"],
                                              axis=1))
    if not parts:
        raise FileNotFoundError(f"no 15m archives for {base}")
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated()]


def verify_checksums(d: Path) -> dict[str, Any]:
    """SHA-256 of every archive against Binance's published .CHECKSUM file."""
    ok, bad, missing = 0, [], []
    for z in sorted(d.glob("*.zip")):
        ck = z.with_name(z.name + ".CHECKSUM")
        if not ck.exists():
            missing.append(z.name)
            continue
        want = ck.read_text().split()[0].lower()
        if hashlib.sha256(z.read_bytes()).hexdigest() == want:
            ok += 1
        else:
            bad.append(z.name)
    return {"verified": ok, "mismatched": bad, "no_checksum": missing}


TICK_MAX_FRACTION = Decimal("0.0001")


def refine_tick(f: Any, low_price: float) -> Any:
    """Today's tick, divided by 10 until it is at most 0.01% of the coin's lowest test-window
    price. Binance quoted low-priced coins (DOGE, SOL at listing) with more decimals in 2020;
    today's coarser tick would distort grid spacing. Fixed before the run, not tuned."""
    tick = f.price_tick
    while tick > Decimal(str(low_price)) * TICK_MAX_FRACTION:
        tick /= 10
    return replace(f, price_tick=tick.normalize())


def hourly(m15: pd.DataFrame) -> pd.DataFrame:
    return m15.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna(subset=["open"])


def daily_closes(spot15: Path, spot1h: Path, base: str) -> pd.Series:
    early = load_spot15(spot15, base)["close"].resample("1D").last()
    late = pd.read_csv(spot1h / f"{base}_1h.csv.gz", parse_dates=["date"]).set_index(
        "date")["close"].resample("1D").last()
    s = pd.concat([early[early.index < pd.Timestamp("2024-01-01", tz="UTC")],
                   late[late.index >= pd.Timestamp("2024-01-01", tz="UTC")]]).dropna()
    return s[~s.index.duplicated()]


def stats(r: pd.Series) -> dict[str, Any]:
    r = r.dropna()
    eq = (1 + r).cumprod()
    sd = float(r.std(ddof=1))
    return {"net_return": float(eq.iloc[-1] - 1), "max_drawdown": float(
        (eq / eq.cummax() - 1).min()), "sharpe": float(r.mean() / sd * math.sqrt(365))
        if sd > 0 else None, "by_year": {str(y): float((1 + g).prod() - 1)
                                         for y, g in r.groupby(r.index.year)}}


# --------------------------------------------------------------------------- 1. grid
def grid_jobs(spot15: Path, exinfo: Path) -> list[dict[str, Any]]:
    sf = bt.spot_filters(exinfo)
    jobs = []
    for b in bt.SPOT:
        m15 = load_spot15(spot15, b)
        h1 = hourly(m15)
        low = float(m15["low"][(m15.index >= GRID_START) & (m15.index < GRID_END)].min())
        flt = refine_tick(sf[b], low)
        for v2 in (False, True):
            for fee in (1, 2):
                jobs.append({"market": "spot", "symbol": f"{b}USDT", "m15": m15, "h1": h1,
                             "params": bt.DEFAULT, "alloc": bt.SPOT_ALLOC, "filters": flt,
                             "start": GRID_START, "end": GRID_END, "fee_mult": fee,
                             "close_on_regime_break": v2, "version": "v2" if v2 else "v1"})
    return jobs


def _run(job: dict[str, Any]) -> dict[str, Any]:
    res = bt.run_symbol(job)
    res["version"] = job["version"]
    res["price_tick"] = str(job["filters"].price_tick)
    return res


def grid_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ver in ("v1", "v2"):
        for fee in ("1", "2"):
            rs = [r for r in results if r["version"] == ver and r["fee_mult"] == fee]
            eq, trips, start = bt.pooled(rs)
            m = bt.metrics(eq, trips, start)
            per = {r["symbol"]: float(bt.curve(r).iloc[-1] / r["alloc"] - 1) for r in rs}
            periods = {}
            for name, a, b in (("2020H2", "2020-07-01", "2021-01-01"),
                               ("2021", "2021-01-01", "2022-01-01"),
                               ("2022", "2022-01-01", "2023-01-01"),
                               ("2023", "2023-01-01", "2024-01-01")):
                periods[name] = bt._window_return(eq, pd.Timestamp(a, tz="UTC"),
                                                  pd.Timestamp(b, tz="UTC"), start)
            best = max(per, key=lambda k: per[k])
            wo = [r for r in rs if r["symbol"] != best]
            e2, _, s2 = bt.pooled(wo)
            by_regime: dict[str, dict[str, float]] = {}
            for t in trips:
                g = by_regime.setdefault(t["regime"], {"trips": 0, "pnl": 0.0})
                g["trips"] += 1
                g["pnl"] += t["pnl"]
            out[f"{ver}_fee{fee}"] = {
                **m, "periods": periods, "per_symbol": per, "best_symbol": best,
                "without_best": float(e2.iloc[-1] / s2 - 1),
                "regime_closes": sum(r.get("regime_closes", 0) for r in rs),
                "stops_total": sum(r["stops"] for r in rs),
                "fees": sum(r["fees"] for r in rs), "by_regime_exit": by_regime}
    v2, v1, v2x = out["v2_fee1"], out["v1_fee1"], out["v2_fee2"]
    gates = {"net_positive": v2["net_return"] > 0,
             "profit_factor_ge_1_1": (v2["profit_factor"] or 0) >= 1.1,
             "drawdown_le_15pct": v2["max_drawdown"] >= -0.15,
             "positive_2_of_4_periods": sum(1 for x in v2["periods"].values() if x > 0) >= 2,
             "positive_at_2x_fees": v2x["net_return"] > 0,
             "positive_without_best": v2["without_best"] > 0,
             "better_than_v1": v2["net_return"] > v1["net_return"]}
    return {"results": out, "gates": gates, "passed": all(gates.values())}


# ---------------------------------------------------------------- 2. volatility target
def vol_target(px: pd.Series, target: float, *, cost: float = COST) -> pd.Series:
    """Daily net returns of a coin/cash portfolio. At each daily close the target weight is
    min(1, target / annualised 30-day realised vol) from closes up to that close; it trades
    to the target only when the held weight (which drifts with the price) is more than 10
    points away. Costs are paid on the traded notional."""
    r = px.pct_change()
    vol = r.rolling(30, min_periods=30).std() * math.sqrt(365)
    want = (target / vol).clip(upper=1.0)
    cash, units, prev = 1.0, 0.0, 1.0
    out = []
    for p, w in zip(px.to_numpy(float), want.to_numpy(float), strict=True):
        eq = cash + units * p
        if not math.isnan(w) and abs(w - units * p / eq) > 0.10:
            trade = w * eq - units * p              # notional bought (+) or sold (-)
            fee = abs(trade) * cost
            units += trade / p
            cash -= trade + fee
            eq -= fee
        out.append(eq / prev - 1)
        prev = eq
    return pd.Series(out, index=px.index)


def trades_count(px: pd.Series, target: float) -> int:
    r = px.pct_change()
    want = (target / (r.rolling(30, min_periods=30).std() * math.sqrt(365))).clip(upper=1.0)
    units, cash, n = 0.0, 1.0, 0
    for p, w in zip(px.to_numpy(float), want.to_numpy(float), strict=True):
        eq = cash + units * p
        if not math.isnan(w) and abs(w - units * p / eq) > 0.10:
            trade = w * eq - units * p
            units += trade / p
            cash -= trade
            n += 1
    return n


def vol_report(closes: dict[str, pd.Series]) -> dict[str, Any]:
    both = pd.concat(closes, axis=1).dropna()
    base = both[both.index >= VOL_START].iloc[0]
    mix = (both / base).mean(axis=1)                       # 50/50 bought at the window start
    assets = {"BTC": both["BTC"], "ETH": both["ETH"], "BTC_ETH_50_50": mix}
    out: dict[str, Any] = {}
    for name, px in assets.items():
        rows: dict[str, Any] = {}
        hold_r = px.pct_change()
        win = (px.index >= VOL_START) & (px.index < VOL_END)
        hold = stats(hold_r[win])
        for tgt in (0.30, 0.40, 0.50):
            vr = vol_target(px, tgt)
            rows[f"target_{int(tgt * 100)}"] = stats(vr[win])
            rows[f"target_{int(tgt * 100)}_2x_costs"] = stats(vol_target(px, tgt,
                                                                         cost=2 * COST)[win])
        main, main2 = rows["target_40"], rows["target_40_2x_costs"]
        sel_all = (px.index >= VOL_START) & (px.index < VOL_END)
        rows["trades_target_40"] = trades_count(px[px.index < VOL_END], 0.40) - \
            trades_count(px[px.index < VOL_START], 0.40)
        rows["avg_weight_target_40"] = float(
            ((0.40 / (px.pct_change().rolling(30, min_periods=30).std() * math.sqrt(365)))
             .clip(upper=1.0))[sel_all].mean())
        halves = {}
        for h, a, b in (("first", VOL_START, VOL_HALF), ("second", VOL_HALF, VOL_END)):
            sel = (px.index >= a) & (px.index < b)
            halves[h] = {"strategy": stats(vol_target(px, 0.40)[sel]),
                         "hold": stats(hold_r[sel])}
        gates = {
            "sharpe_above_hold": (main["sharpe"] or 0) > (hold["sharpe"] or 0),
            "drawdown_10pts_smaller": main["max_drawdown"] >= hold["max_drawdown"] + 0.10,
            "net_positive": main["net_return"] > 0,
            "sharpe_above_hold_both_halves": all(
                (v["strategy"]["sharpe"] or 0) > (v["hold"]["sharpe"] or 0)
                for v in halves.values()),
            "holds_at_2x_costs": (main2["sharpe"] or 0) > (hold["sharpe"] or 0)
            and main2["net_return"] > 0}
        out[name] = {"hold": hold, **rows, "halves": halves, "gates": gates,
                     "passed": all(gates.values())}
    return out


# ------------------------------------------------------------------------- 3. pairs
def pairs(fut: Path, *, cost_mults: tuple[float, ...] = (1.0, 2.0)) -> dict[float, Any]:
    """One pass of signals (default ``PairConfig``), charged at each cost multiple: the
    stress run keeps the same trades and pays more, as in the adaptive-grid stress."""
    eth15, _, f_eth = bt.load_futures(fut, "ETHUSDT")
    btc15, _, f_btc = bt.load_futures(fut, "BTCUSDT")
    a = eth15["close"].resample("1h").last()      # bar labelled t closes at t + 1h
    b = btc15["close"].resample("1h").last()
    df = pd.concat({"a": a, "b": b}, axis=1).dropna()
    df = df[df.index < PAIR_END]
    cfg = stat_arb.PairConfig()
    per_side = cfg.fee_rate + cfg.slippage_rate
    # Funding settlements bucketed by the hourly bar they fall in: (t, t + 1h].
    fund: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    for leg, ser in (("a", f_eth), ("b", f_btc)):
        for ts, rate in ser.items():
            if not math.isnan(rate):
                fund.setdefault((ts - pd.Timedelta(microseconds=1)).floor("1h"), []).append(
                    (leg, float(rate)))
    books: dict[float, dict[str, Any]] = {m: {"cash": 10_000.0, "curve": {}, "fees": 0.0,
                                              "funding": 0.0}
             for m in cost_mults}
    pos: dict[str, Any] | None = None
    co: stat_arb.CointResult | None = None
    trades: list[dict[str, Any]] = []
    refits, cointegrated_fits = 0, 0
    idx = df.index
    first = int(idx.searchsorted(PAIR_START))
    pa_all, pb_all = df["a"].to_numpy(float), df["b"].to_numpy(float)
    for i in range(first, len(idx)):
        t = idx[i]
        if (i - first) % 168 == 0:
            form = df.iloc[max(0, i - 720):i]
            refits += 1
            try:
                co = stat_arb.engle_granger(form["a"], form["b"], a="ETH", b="BTC")
                cointegrated_fits += int(co.cointegrated)
            except ValueError:
                co = None
        pa, pb = pa_all[i], pb_all[i]
        if pos is not None:                       # held through this bar: pay/receive funding
            for leg, rate in fund.get(t, []):
                q, px = (pos["qa"], pa) if leg == "a" else (pos["qb"], pb)
                for bk in books.values():         # funding is not a cost multiple
                    bk["cash"] -= q * px * rate
                    bk["funding"] -= q * px * rate
        use = pos["co"] if pos is not None else co
        if use is not None:
            hist = df.iloc[max(0, i - 200):i + 1]
            sig = stat_arb.pair_signal(hist["a"], hist["b"], use,
                                       in_position=pos["side"] if pos else None, cfg=cfg)
            if pos is None and sig.decision in ("LONG_SPREAD", "SHORT_SPREAD"):
                sgn = 1.0 if sig.decision == "LONG_SPREAD" else -1.0
                eq0 = books[cost_mults[0]]["cash"]
                notional = eq0 / (1 + abs(use.beta))          # 1x gross on the equity
                qa = sgn * notional / pa
                qb = -sgn * use.beta * notional / pb
                for m, bk in books.items():
                    fee = (abs(qa) * pa + abs(qb) * pb) * per_side * m
                    bk["cash"] -= fee
                    bk["fees"] += fee
                pos = {"side": sig.decision, "qa": qa, "qb": qb, "pa": pa, "pb": pb,
                       "co": use, "t": t, "z": sig.z}
            elif pos is not None and sig.decision in ("EXIT", "STOP"):
                pnl = pos["qa"] * (pa - pos["pa"]) + pos["qb"] * (pb - pos["pb"])
                for m, bk in books.items():
                    fee = (abs(pos["qa"]) * pa + abs(pos["qb"]) * pb) * per_side * m
                    bk["cash"] += pnl - fee
                    bk["fees"] += fee
                trades.append({"entry": str(pos["t"]), "exit": str(t), "side": pos["side"],
                               "kind": sig.decision, "entry_z": pos["z"], "exit_z": sig.z,
                               "pnl_gross": pnl,
                               "hours": (t - pos["t"]).total_seconds() / 3600})
                pos = None
        mtm = 0.0 if pos is None else pos["qa"] * (pa - pos["pa"]) + pos["qb"] * (
            pb - pos["pb"])
        if t.hour == 23:
            for bk in books.values():
                bk["curve"][t.normalize()] = bk["cash"] + mtm
    out: dict[float, Any] = {}
    for m, bk in books.items():
        s_ = pd.Series(bk["curve"])
        r = s_.pct_change()
        r.iloc[0] = s_.iloc[0] / 10_000 - 1
        out[m] = {"stats": stats(r), "daily_returns": r, "fees": bk["fees"],
                  "funding": bk["funding"]}
    common = {"trades": len(trades), "stops": sum(1 for x in trades if x["kind"] == "STOP"),
              "long_spread": sum(1 for x in trades if x["side"] == "LONG_SPREAD"),
              "avg_hours": float(np.mean([x["hours"] for x in trades])) if trades else None,
              "gross_pnl": float(sum(x["pnl_gross"] for x in trades)),
              "refits": refits, "cointegrated_refits": cointegrated_fits,
              "open_at_end": pos is not None, "trade_log": trades[-20:]}
    for m in out:
        out[m].update(common)
    return out


def pairs_report(fut: Path) -> dict[str, Any]:
    runs = pairs(fut)
    base, stress = runs[1.0], runs[2.0]
    st = base["stats"]
    p = stationary_bootstrap_pvalue(base["daily_returns"].to_numpy(), simulations=5000,
                                    block=10)
    gates = {"net_positive": st["net_return"] > 0,
             "sharpe_ge_0_5": (st["sharpe"] or 0) >= 0.5,
             "drawdown_le_15pct": st["max_drawdown"] >= -0.15,
             "significant_p_lt_0_05": p is not None and p < 0.05,
             "positive_2_of_3_years": sum(1 for v in st["by_year"].values() if v > 0) >= 2,
             "positive_at_2x_costs": stress["stats"]["net_return"] > 0}
    return {"stats": st, "p_value": p, "trades": base["trades"], "stops": base["stops"],
            "long_spread": base["long_spread"], "avg_hours": base["avg_hours"],
            "gross_pnl": base["gross_pnl"], "fees": base["fees"], "funding": base["funding"],
            "refits": base["refits"], "cointegrated_refits": base["cointegrated_refits"],
            "open_at_end": base["open_at_end"], "stress_2x": stress["stats"],
            "recent_trades": base["trade_log"], "gates": gates, "passed": all(gates.values())}


def vol_target_ledger(px: pd.Series, target: float, *, start: pd.Timestamp | None = None,
                      initial: float = 1.0, cost: float = COST) -> pd.DataFrame:
    """The ``vol_target`` rule as a ledger (equity, held weight, target, trades), holding
    nothing before ``start``. Used by the paper forward test; the same arithmetic as
    ``vol_target`` (a unit test checks they agree)."""
    r = px.pct_change()
    vol = r.rolling(30, min_periods=30).std() * math.sqrt(365)
    want = (target / vol).clip(upper=1.0)
    cash, units = initial, 0.0
    rows = []
    for d, p, w, v in zip(px.index, px.to_numpy(float), want.to_numpy(float),
                          vol.to_numpy(float), strict=True):
        if start is not None and d < start:
            continue
        eq = cash + units * p
        trade = fee = 0.0
        if not math.isnan(w) and abs(w - units * p / eq) > 0.10:
            trade = w * eq - units * p
            fee = abs(trade) * cost
            units += trade / p
            cash -= trade + fee
            eq -= fee
        rows.append({"date": d, "equity": eq, "weight": units * p / eq,
                     "target": None if math.isnan(w) else float(w),
                     "vol": None if math.isnan(v) else float(v), "trade": trade, "fee": fee})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(
        columns=["equity", "weight", "target", "vol", "trade", "fee"])


def combine(grid: dict[str, Any], vol: dict[str, Any], pairs_: dict[str, Any], *,
            harness_commit: str, generated_at: str) -> dict[str, Any]:
    """The committed result file shown in the app."""
    return {
        "generated_at": generated_at, "harness_commit": harness_commit,
        "preregistration": "docs/research/2026-11-26-batch3-preregistration.md",
        "paper": False, "kind": "historical backtest (no orders placed)",
        "data": {"grid": "Binance spot 15m klines 2020-01..2023-12, 10 coins, 465 archives, "
                         "checksums verified",
                 "vol_target": "Binance spot daily closes 2020-01..2026-10 (15m archives to "
                               "2023-12, hourly klines after), checksums verified",
                 "pairs": "Binance USD-M perpetual 15m klines and funding 2023-06..2026-10, "
                          "164 archives, checksums verified"},
        "windows": {"grid": ["2020-07-01", "2023-12-31"],
                    "vol_target": ["2020-07-01", "2026-10-31"],
                    "pairs": ["2024-01-01", "2026-10-31"]},
        "grid_v2": grid, "vol_target": vol, "pairs": pairs_,
        "summary": {"grid_v2_passed": grid["passed"],
                    "vol_target_passed": [k for k, v in vol.items() if v["passed"]],
                    "pairs_passed": pairs_["passed"]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot15", type=Path)
    ap.add_argument("--spot1h", type=Path)
    ap.add_argument("--futures", type=Path)
    ap.add_argument("--exinfo", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--part", choices=("grid", "vol", "pairs", "combine"), required=True)
    ap.add_argument("--parts", type=Path, help="combine: directory with grid/vol/pairs.json")
    ap.add_argument("--commit", default="")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.part == "combine":
        from datetime import UTC, datetime
        parts = {k: json.loads((a.parts / f"{k}.json").read_text())
                 for k in ("grid", "vol", "pairs")}
        a.out.write_text(json.dumps(combine(parts["grid"], parts["vol"], parts["pairs"],
                                            harness_commit=a.commit,
                                            generated_at=datetime.now(UTC).isoformat()),
                                    indent=1, default=str))
        return
    if a.part == "pairs":
        res = pairs_report(a.futures)
    elif a.part == "vol":
        res = vol_report({c: daily_closes(a.spot15, a.spot1h, c) for c in ("BTC", "ETH")})
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            res = grid_report(list(ex.map(_run, grid_jobs(a.spot15, a.exinfo))))
    a.out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k in ("gates", "passed")}, default=str)
          if a.part != "vol" else json.dumps({k: (v["gates"], v["passed"])
                                              for k, v in res.items()}, default=str))


if __name__ == "__main__":
    main()
