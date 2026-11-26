"""Out-of-sample test of the public Freqtrade strategy collection, run by Freqtrade itself.

Pre-registration: docs/research/2026-11-23-freqtrade-community-preregistration.md.

* ``data``: official Binance spot 5m klines (data.binance.vision), SHA-256 checked, written
  as Freqtrade feather files, plus 15m/30m/1h/4h/12h/1d resampled from 5m.
* ``run``: for every eligible strategy, ``freqtrade backtesting`` from a separate
  environment (Freqtrade is not a NEXUS dependency), at 0.1% and 0.2% fees per side.
* ``report``: gates, Holm-corrected trade bootstrap, benchmarks, JSON for the dashboard.

The GPL-3.0 strategy files stay in a scratch directory: nothing from them is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/spot/monthly/klines"
PAIRS = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "LTC")
FIRST_MONTH, LAST_MONTH = "2023-06", "2026-10"
OOS_START, OOS_END = "20240101", "20261101"
RESAMPLE = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "12h": "12h",
            "1d": "1D"}
EXCLUDE_DIRS = ("lookahead_bias", "futures")
EXCLUDE_NAMES = {"TWAPStrategy", "AlmgrenChrissStrategy", "DoesNothingStrategy"}
CHANGED_IN_WINDOW = {"Supertrend", "TrendRiderStrategy"}
FEES = (0.001, 0.002)
WALLET = 10_000.0


RESULT_FILE = Path(__file__).with_name("results") / "freqtrade_community_2026-11.json"


def load_result() -> dict[str, Any] | None:
    """The committed result of the single pre-registered run (None if absent)."""
    if not RESULT_FILE.exists():
        return None
    data: dict[str, Any] = json.loads(RESULT_FILE.read_text())
    return data


# ------------------------------------------------------------------------------ data
def months(start: str, end: str) -> list[str]:
    out, y, m = [], int(start[:4]), int(start[5:])
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _client() -> Any:
    import httpx

    verify: Any = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True
    return httpx.Client(timeout=60, verify=verify, follow_redirects=True)


def parse_kline_zip(data: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) != 1 or "/" in members[0].filename:
            raise ValueError("unexpected archive layout")
        raw = zf.read(members[0])
    df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6),
                     names=["t", "open", "high", "low", "close", "volume"])
    df = df[pd.to_numeric(df["t"], errors="coerce").notna()].astype(float)
    # Binance spot archives switched from milliseconds to microseconds in 2025.
    unit = np.where(df["t"] > 1e14, "us", "ms")
    t = np.where(unit == "us", df["t"] // 1000, df["t"]).astype("int64")
    df["date"] = pd.to_datetime(t, unit="ms", utc=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def download(cache: Path) -> dict[str, int]:
    """Fetch every monthly 5m archive once (cached), checking SHA-256 where published."""
    cache.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "cached": 0, "checksum_ok": 0, "no_checksum": 0}
    with _client() as c:
        for base in PAIRS:
            sym = f"{base}USDT"
            for mo in months(FIRST_MONTH, LAST_MONTH):
                name = f"{sym}-5m-{mo}.zip"
                path = cache / name
                if path.exists():
                    stats["cached"] += 1
                else:
                    r = c.get(f"{BASE}/{sym}/5m/{name}")
                    if r.status_code != 200:
                        raise RuntimeError(f"missing archive {name}: HTTP {r.status_code}")
                    path.write_bytes(r.content)
                    stats["downloaded"] += 1
                ck = cache / (name + ".CHECKSUM")
                if not ck.exists():
                    r = c.get(f"{BASE}/{sym}/5m/{name}.CHECKSUM")
                    if r.status_code == 200:
                        ck.write_text(r.text)
                if ck.exists():
                    want = ck.read_text().split()[0]
                    if hashlib.sha256(path.read_bytes()).hexdigest() != want:
                        path.unlink()
                        raise RuntimeError(f"checksum mismatch: {name}")
                    stats["checksum_ok"] += 1
                else:
                    stats["no_checksum"] += 1
    return stats


def build_datadir(cache: Path, datadir: Path) -> dict[str, int]:
    """Freqtrade feather files: <BASE>_USDT-<tf>.feather for 5m and every resampled tf."""
    import pyarrow.feather as feather  # type: ignore[import-not-found]  # run with Freqtrade env

    datadir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for base in PAIRS:
        parts = [parse_kline_zip((cache / f"{base}USDT-5m-{mo}.zip").read_bytes())
                 for mo in months(FIRST_MONTH, LAST_MONTH)]
        df = pd.concat(parts).drop_duplicates("date").sort_values("date")
        df = df.reset_index(drop=True)
        gaps = df["date"].diff().dt.total_seconds().gt(300).sum()
        rows[f"{base}:5m_gaps"] = int(gaps)         # exchange outages, reported not filled
        feather.write_feather(df, datadir / f"{base}_USDT-5m.feather", compression="lz4")
        rows[f"{base}:5m"] = len(df)
        s = df.set_index("date")
        for tf, rule in RESAMPLE.items():
            r = s.resample(rule, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"}).dropna(subset=["open"]).reset_index()
            feather.write_feather(r, datadir / f"{base}_USDT-{tf}.feather", compression="lz4")
    return rows


# ------------------------------------------------------------------------- strategies
def inventory(repo: Path) -> list[dict[str, Any]]:
    """Every strategy class with its file, timeframe, publication date and eligibility."""
    root = repo / "user_data" / "strategies"
    out = []
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root)
        src = f.read_text(errors="replace")
        classes = re.findall(r"^class\s+(\w+)\s*\(\s*(?:IStrategy|\w*Strategy)\s*\)", src, re.M)
        if not classes:
            continue
        tf = re.search(r"^\s*timeframe\s*=\s*['\"](\w+)['\"]", src, re.M)
        git = shutil.which("git") or "git"
        added = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [git, "-C", str(repo), "log", "--diff-filter=A", "--follow", "--format=%cs",
             "--", str(f.relative_to(repo))], capture_output=True, text=True).stdout.split()
        for cls in classes:
            row: dict[str, Any] = {"strategy": cls, "file": str(rel),
                                   "timeframe": tf.group(1) if tf else None,
                                   "published": added[-1] if added else None,
                                   "changed_in_window": cls in CHANGED_IN_WINDOW}
            reason = None
            if rel.parts[0] in EXCLUDE_DIRS:
                reason = f"excluded folder {rel.parts[0]}/"
            elif cls in EXCLUDE_NAMES:
                reason = "execution algorithm or template, not a trading signal"
            elif re.search(r"^\s*can_short\s*=\s*True", src, re.M):
                reason = "short selling (futures disabled in NEXUS)"
            elif row["timeframe"] == "1m":
                reason = "1-minute timeframe (no 1m history in this test)"
            row["excluded"] = reason
            out.append(row)
    return out


def window(published: str | None) -> tuple[str, bool]:
    if not published or published < "2024-01-01":
        return OOS_START, False
    d = datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=UTC)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return f"{y:04d}{m:02d}01", True


def ft_config(path: Path, datadir: Path, fee: float) -> None:
    cfg = {
        "max_open_trades": 3, "stake_currency": "USDT", "stake_amount": "unlimited",
        "tradable_balance_ratio": 0.99, "dry_run": True, "dry_run_wallet": WALLET,
        "fiat_display_currency": "", "trading_mode": "spot", "margin_mode": "",
        "fee": fee, "dataformat_ohlcv": "feather", "datadir": str(datadir),
        "exchange": {"name": "binance", "key": "", "secret": "",
                     "pair_whitelist": [f"{b}/USDT" for b in PAIRS], "pair_blacklist": [],
                     "skip_pair_validation": True},
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "same", "use_order_book": False},
        "exit_pricing": {"price_side": "same", "use_order_book": False},
        "bot_name": "nexus-oos", "initial_state": "running", "internals": {},
    }
    path.write_text(json.dumps(cfg, indent=1))


def run_one(ft_python: Path, repo: Path, row: dict[str, Any], datadir: Path, work: Path,
            fee: float, exinfo: Path) -> dict[str, Any]:
    """One Freqtrade backtest, run by Freqtrade's own interpreter through
    ``freqtrade_offline`` (market rules from the saved ``exinfo`` file, no exchange call)."""
    start, short = window(row["published"])
    cfg = work / f"config-{fee}.json"
    ft_config(cfg, datadir, fee)
    out = work / f"{row['strategy']}-{fee}.json"
    spath = repo / "user_data" / "strategies" / Path(row["file"]).parent
    offline = Path(__file__).with_name("freqtrade_offline.py")
    cmd = [str(ft_python), "-I", str(offline), str(exinfo), "backtesting", "-c", str(cfg),
           "--strategy", row["strategy"],
           "--strategy-path", str(spath), "--timerange", f"{start}-{OOS_END}",
           "--export", "trades", "--export-filename", str(out), "--cache", "none",
           "--userdir", str(work / "ud"), "--datadir", str(datadir), "--fee", str(fee)]
    (work / "ud").mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,  # noqa: S603
                       env={**os.environ, "FREQTRADE__EXCHANGE__KEY": ""})
    res: dict[str, Any] = {"strategy": row["strategy"], "fee": fee, "start": start,
                           "short_window": short, "returncode": p.returncode}
    if p.returncode != 0:
        err = [ln for ln in (p.stderr + p.stdout).splitlines() if ln.strip()]
        res["error"] = " | ".join(err[-3:])[:600]
        return res
    res["trades"] = load_trades(work / "ud" / "backtest_results", row["strategy"])
    return res


def load_trades(results_dir: Path, strategy: str) -> list[dict[str, Any]]:
    """Trades of the latest backtest result for ``strategy`` (Freqtrade zip or json)."""
    last = results_dir / ".last_result.json"
    name = json.loads(last.read_text())["latest_backtest"]
    path = results_dir / name
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            member = next(n for n in zf.namelist() if n.endswith(".json")
                          and "config" not in n and "_market_change" not in n)
            data = json.loads(zf.read(member))
    else:
        data = json.loads(path.read_text())
    trades = data["strategy"][strategy]["trades"]
    keep = ("pair", "open_date", "close_date", "profit_ratio", "profit_abs", "fee_open",
            "fee_close", "stake_amount", "exit_reason", "trade_duration")
    return [{k: t.get(k) for k in keep} for t in trades]


# ---------------------------------------------------------------------------- report
def bootstrap_p(x: np.ndarray, sims: int = 10_000, seed: int = 7) -> float | None:
    """One-sided p-value that the mean per-trade return is > 0 (resampled trades,
    centred under the null)."""
    if len(x) < 10:
        return None
    rng = np.random.default_rng(seed)
    centred = x - x.mean()
    idx = rng.integers(0, len(x), size=(sims, len(x)))
    means = centred[idx].mean(axis=1)
    return float((np.sum(means >= x.mean()) + 1) / (sims + 1))


def holm(p: dict[str, float | None], alpha: float = 0.05) -> dict[str, bool]:
    items = sorted([(v, k) for k, v in p.items() if v is not None])
    m, out, ok = len(items), {k: False for k in p}, True
    for i, (v, k) in enumerate(items):
        ok = ok and v <= alpha / (m - i)
        out[k] = ok
    return out


def stats(trades: list[dict[str, Any]], start: str) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "net_profit": 0.0, "net_return": 0.0}
    df = pd.DataFrame(trades)
    df["close_date"] = pd.to_datetime(df["close_date"], utc=True)
    df = df.sort_values("close_date")
    pnl = df["profit_abs"].astype(float)
    eq = WALLET + pnl.cumsum()
    peak = np.maximum.accumulate(np.r_[WALLET, eq.to_numpy()])[1:]
    dd = float(((eq - peak) / peak).min())
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    years = {str(y): float(g["profit_abs"].sum()) for y, g in df.groupby(df.close_date.dt.year)}
    daily = (pnl.groupby(df.close_date.dt.floor("D")).sum()
             .reindex(pd.date_range(pd.Timestamp(start, tz="UTC"),
                                    pd.Timestamp(OOS_END, tz="UTC"), freq="D",
                                    inclusive="left"), fill_value=0.0)) / WALLET
    sd = float(daily.std(ddof=1))
    neg = daily[daily < 0]
    by_pair = df.groupby("pair")["profit_abs"].sum().sort_values()
    return {"trades": len(df), "net_profit": float(pnl.sum()),
            "net_return": float(pnl.sum() / WALLET),
            "profit_factor": float(wins / losses) if losses > 0 else None,
            "win_rate": float((pnl > 0).mean()), "max_drawdown": dd,
            "avg_trade_return": float(df["profit_ratio"].astype(float).mean()),
            "avg_duration_min": float(pd.to_numeric(df["trade_duration"]).mean()),
            "fees_paid": float(((df["fee_open"].astype(float) + df["fee_close"].astype(float))
                                * df["stake_amount"].astype(float)).sum()),
            "by_year": years,
            "sharpe": float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "sortino": float(daily.mean() / math.sqrt((neg ** 2).mean()) * math.sqrt(365))
            if len(neg) else None,
            "best_pair": [by_pair.index[-1], float(by_pair.iloc[-1])],
            "worst_pair": [by_pair.index[0], float(by_pair.iloc[0])]}


def gates(base: dict[str, Any], stress: dict[str, Any] | None) -> list[str]:
    fails = []
    if base["net_profit"] <= 0:
        fails.append("net profit <= 0")
    pf = base.get("profit_factor")
    if pf is None or pf < 1.2:
        fails.append(f"profit factor {pf if pf is None else round(pf, 2)} < 1.2")
    if base.get("max_drawdown", 0) < -0.25:
        fails.append(f"max drawdown {base['max_drawdown']:.1%} worse than -25%")
    if base["trades"] < 50:
        fails.append(f"{base['trades']} trades < 50")
    yrs = base.get("by_year", {})
    if sum(1 for y in ("2024", "2025", "2026") if yrs.get(y, 0) > 0) < 2:
        fails.append("positive in fewer than 2 of 2024 / 2025 / 2026")
    if stress is None or stress.get("net_profit", 0) <= 0:
        fails.append("not profitable at 2x fees")
    return fails


def benchmarks(datadir: Path) -> dict[str, Any]:
    import pyarrow.feather as feather

    s, e = pd.Timestamp(OOS_START, tz="UTC"), pd.Timestamp(OOS_END, tz="UTC")
    rets = {}
    for b in PAIRS:
        d = feather.read_table(datadir / f"{b}_USDT-1d.feather").to_pandas().set_index("date")
        d = d[(d.index >= s) & (d.index < e)]
        rets[b] = float(d["close"].iloc[-1] / d["open"].iloc[0] - 1)
    return {"btc_hold": rets["BTC"], "equal_weight_hold": float(np.mean(list(rets.values()))),
            "per_coin": rets}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("data", "run", "report"))
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--ft-python", type=Path, help="python of the Freqtrade environment")
    ap.add_argument("--exinfo", type=Path, help="saved Binance exchangeInfo JSON")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    cache, datadir = a.work / "cache", a.work / "data" / "binance"
    if a.step == "data":
        print(json.dumps(download(cache)))
        print(json.dumps(build_datadir(cache, datadir)))
        return
    if a.step == "run":
        inv = inventory(a.repo)
        (a.work / "inventory.json").write_text(json.dumps(inv, indent=1))
        runs = a.work / "runs"
        runs.mkdir(exist_ok=True)
        for row in inv:
            if row["excluded"] or (a.only and row["strategy"] not in a.only):
                continue
            for fee in FEES:
                dest = runs / f"{row['strategy']}-{fee}.result.json"
                if dest.exists():
                    continue
                w = a.work / "tmp" / f"{row['strategy']}-{fee}"
                w.mkdir(parents=True, exist_ok=True)
                res = run_one(a.ft_python, a.repo, row, datadir, w, fee, a.exinfo)
                dest.write_text(json.dumps(res))
                print(row["strategy"], fee, res.get("error") or f"{len(res['trades'])} trades",
                      flush=True)
        return
    inv = json.loads((a.work / "inventory.json").read_text())
    rows, pvals = [], {}
    for row in inv:
        out = {k: row[k] for k in ("strategy", "file", "timeframe", "published",
                                   "changed_in_window", "excluded")}
        if row["excluded"]:
            rows.append(out)
            continue
        r1 = a.work / "runs" / f"{row['strategy']}-0.001.result.json"
        r2 = a.work / "runs" / f"{row['strategy']}-0.002.result.json"
        if not r1.exists():
            out["excluded"] = "not run"
            rows.append(out)
            continue
        base = json.loads(r1.read_text())
        stress = json.loads(r2.read_text()) if r2.exists() else None
        out["window_start"], out["short_window"] = base["start"], base["short_window"]
        if base.get("error"):
            out["error"] = base["error"]
            rows.append(out)
            continue
        s1 = stats(base["trades"], base["start"])
        s2 = (stats(stress["trades"], stress["start"])
              if stress and not stress.get("error") else None)
        out["stats"], out["stress_2x_fees"] = s1, (
            {k: s2[k] for k in ("net_profit", "net_return", "trades")} if s2 else None)
        out["gate_failures"] = gates(s1, s2)
        x = np.array([t["profit_ratio"] for t in base["trades"]], dtype=float)
        pvals[row["strategy"]] = bootstrap_p(x)
        out["bootstrap_p"] = pvals[row["strategy"]]
        rows.append(out)
    sig = holm(pvals)
    for r in rows:
        if "gate_failures" in r:
            if not sig.get(r["strategy"], False):
                r["gate_failures"].append("not significant after Holm correction")
            r["passed"] = not r["gate_failures"]
    tested = [r for r in rows if "stats" in r]
    tested.sort(key=lambda r: -r["stats"]["net_return"])
    report = {"generated_at": datetime.now(UTC).isoformat(),
              "preregistration": "docs/research/2026-11-23-freqtrade-community-preregistration.md",
              "source": "github.com/freqtrade/freqtrade-strategies@f3340ce",
              "engine": "freqtrade 2026.9 backtesting", "window": [OOS_START, OOS_END],
              "fees": FEES, "pairs": [f"{b}/USDT" for b in PAIRS], "wallet": WALLET,
              "benchmarks": benchmarks(datadir),
              "summary": {"files": len({r['file'] for r in rows}), "strategies": len(rows),
                          "tested": len(tested),
                          "errors": sum(1 for r in rows if r.get("error")),
                          "excluded": sum(1 for r in rows if r.get("excluded")),
                          "profitable": sum(1 for r in tested if r["stats"]["net_profit"] > 0),
                          "passed": sum(1 for r in tested if r.get("passed"))},
              "ranked": tested,
              "not_tested": [r for r in rows if "stats" not in r]}
    (a.out or a.work / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
