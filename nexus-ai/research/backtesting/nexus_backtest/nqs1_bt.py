"""NQS-1 research harness: walk-forward optimisation, out-of-sample metrics, Monte Carlo,
parameter sensitivity, the per-strategy vs adaptive comparison, the optional ML filter,
and a 2x cost stress.

Pre-registration: docs/research/2026-11-27-nqs1-preregistration.md. The gates, the grid
and the windows below are fixed there before any real data was seen.

**Input.** A directory of CSV files ``<PAIR>_<TF>.csv`` with columns
``time,open,high,low,close`` (UTC, bar open time), as exported from the owner's
MetaTrader 5 terminal by the NEXUS bridge (``--export-history``). Optionally
``carry.csv`` with a ``date`` column and one column per pair: the annual interest-rate
differential, base minus quote, as a decimal.

Usage: ``python -m nexus_backtest.nqs1_bt --data D --profile intraday --out F``
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nexus_strategies.nqs1 import ml
from nexus_strategies.nqs1.config import PAIRS, PROFILES, NQS1Config, Profile
from nexus_strategies.nqs1.engine import Portfolio, Position, choose, prepare
from nexus_strategies.nqs1.features import resample

from nexus_backtest.significance import stationary_bootstrap_pvalue

Data = dict[str, tuple[pd.DataFrame, pd.DataFrame]]

MODES = ("adaptive", "tsmom", "trend", "breakout", "mean_reversion")
TRAIN_MONTHS, TEST_MONTHS = 24, 6
WARMUP_MONTHS = 3
GRID = {"stop_scale": (0.8, 1.0, 1.25), "take_profit_r": (1.5, 2.0, 3.0),
        "adx_strong": (22.0, 25.0, 28.0)}
DEFAULTS = {"stop_scale": 1.0, "take_profit_r": 2.0, "adx_strong": 25.0}
MIN_TRAIN_TRADES = 30
MIN_MONTHS = WARMUP_MONTHS + TRAIN_MONTHS + TEST_MONTHS
EQUITY = 10_000.0


# ------------------------------------------------------------------------------ data
def load_dir(d: Path, profile: Profile, pairs: tuple[str, ...] = PAIRS) -> Data:
    """(signal bars, trend bars) per pair. The trend timeframe is read if exported,
    otherwise resampled from the signal bars."""
    out = {}
    for pair in pairs:
        f = d / f"{pair}_{profile.signal_tf}.csv"
        if not f.exists():
            continue
        sig = read_bars(f)
        tf = d / f"{pair}_{profile.trend_tf}.csv"
        trend = read_bars(tf) if tf.exists() else resample(sig, profile.trend_tf)
        out[pair] = (sig, trend)
    return out


def read_bars(f: Path) -> pd.DataFrame:
    df = pd.read_csv(f)
    df.index = pd.to_datetime(df["time"], utc=True)
    df = df[["open", "high", "low", "close"]].astype(float).sort_index()
    df = df[~df.index.duplicated()]
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | \
        (df["low"] > df[["open", "close"]].min(axis=1)) | (df <= 0).any(axis=1)
    return df[~bad]


def load_carry(d: Path) -> dict[str, pd.Series]:
    f = d / "carry.csv"
    if not f.exists():
        return {}
    df = pd.read_csv(f)
    df.index = pd.to_datetime(df["date"], utc=True)
    return {c: df[c].astype(float).dropna() for c in df.columns if c != "date"}


def coverage_months(data: Data) -> float:
    first = min(s.index[0] for s, _ in data.values())
    last = max(s.index[-1] for s, _ in data.values())
    return float((last - first).days / 30.44)


# --------------------------------------------------------------------------- running
def config_for(params: dict[str, float]) -> NQS1Config:
    base = NQS1Config()
    return replace(base,
                   regime=replace(base.regime, adx_strong=params["adx_strong"]),
                   exits=replace(base.exits, take_profit_r=params["take_profit_r"]))


def frames_for(data: Data, profile: Profile, mode: str, params: dict[str, float],
               start: pd.Timestamp, end: pd.Timestamp, p_win: dict[str, float] | None = None
               ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Decision frames and picks on [start, end). Indicators use only bars before
    ``end`` (and each value only bars up to its own close), so nothing from the future
    leaks in; bars before ``start`` serve as warm-up."""
    cfg = config_for(params)
    frames, picks = {}, {}
    for pair, (sig, trend) in data.items():
        f = prepare(sig[sig.index < end], trend[trend.index < end], profile, cfg)
        pk = choose(f, mode, cfg)
        pk["stop_atr"] = pk["stop_atr"] * params["stop_scale"]
        pk["target_r"] = params["take_profit_r"]
        pk["p_win"] = pk["strategy"].map(p_win or {}).astype(float)
        sel = (f.index >= start) & (f.index < end)
        if sel.any():
            frames[pair], picks[pair] = f[sel], pk[sel]
    return frames, picks


def simulate(frames: dict[str, pd.DataFrame], picks: dict[str, pd.DataFrame],
             profile: Profile, *, carry: dict[str, pd.Series] | None = None,
             cost_mult: float = 1.0, veto: Callable[[Position, float], bool] | None = None
             ) -> dict[str, Any]:
    if not frames:
        return {"trades": [], "curve": [], "rejections": {}, "halted": None}
    book = Portfolio(frames, picks, profile, NQS1Config(), equity=EQUITY, carry=carry,
                     cost_mult=cost_mult, veto=veto).run()
    return {"trades": book.trades, "curve": book.curve, "rejections": book.rejections,
            "halted": book.halted}


def run(data: Data, profile: Profile, mode: str, params: dict[str, float],
        start: pd.Timestamp, end: pd.Timestamp, *, carry: dict[str, pd.Series] | None = None,
        cost_mult: float = 1.0) -> dict[str, Any]:
    frames, picks = frames_for(data, profile, mode, params, start, end)
    return simulate(frames, picks, profile, carry=carry, cost_mult=cost_mult)


# --------------------------------------------------------------------------- metrics
def metrics(res: dict[str, Any]) -> dict[str, Any]:
    tr = res["trades"]
    out: dict[str, Any] = {"trades": len(tr), "halted": res.get("halted"),
                           "rejections": res.get("rejections", {})}
    if not res["curve"]:
        return out
    curve = pd.Series([v for _, v in res["curve"]],
                      index=pd.DatetimeIndex([t for t, _ in res["curve"]]))
    curve = curve[~curve.index.duplicated(keep="last")]
    r = np.array([t["r"] for t in tr]) if tr else np.array([])
    pnl = np.array([t["pnl_usd"] for t in tr]) if tr else np.array([])
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    daily = curve.resample("1D").last().dropna()
    dr = daily.pct_change().dropna()
    sd = float(dr.std()) if len(dr) > 2 else 0.0
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1 / 365.25)
    growth = float(curve.iloc[-1] / EQUITY)
    out.update(
        start=str(curve.index[0]), end=str(curve.index[-1]),
        net_return=growth - 1,
        annual_return=growth ** (1 / years) - 1 if growth > 0 else -1.0,
        sharpe=float(dr.mean() / sd * math.sqrt(252)) if sd > 0 else None,
        max_drawdown=float(min(0.0, (curve / np.maximum(curve.cummax(), EQUITY) - 1).min())),
        win_rate=float((r > 0).mean()) if len(r) else None,
        expectancy_r=float(r.mean()) if len(r) else None,
        profit_factor=float(wins / losses) if losses > 0 else None,
        avg_bars_held=float(np.mean([t["bars"] for t in tr])) if tr else None,
        costs_usd=float(sum(t["costs_usd"] for t in tr)),
        carry_usd=float(sum(t["carry_usd"] for t in tr)),
        by_strategy=_group(tr, "strategy"), by_regime=_group(tr, "regime"),
        by_pair=_group(tr, "pair"), exits=_count(tr, "reason"),
        by_year={str(y): float(g.iloc[-1] / g.iloc[0] - 1) if len(g) > 1 else 0.0
                 for y, g in curve.groupby(curve.index.year)})
    return out


def _group(tr: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    g: dict[str, list[float]] = {}
    for t in tr:
        g.setdefault(str(t[key]), []).append(t["r"])
    return {k: {"trades": len(v), "expectancy_r": float(np.mean(v)),
                "win_rate": float(np.mean([x > 0 for x in v]))} for k, v in g.items()}


def _count(tr: list[dict[str, Any]], key: str) -> dict[str, int]:
    c: dict[str, int] = {}
    for t in tr:
        c[str(t[key])] = c.get(str(t[key]), 0) + 1
    return c


def win_rates(trades: list[dict[str, Any]]) -> dict[str, float]:
    """Per-strategy win probability from TRAINING trades, shrunk towards 1/2
    (Laplace: (wins + 1) / (n + 2))."""
    out = {}
    for s, g in _group(trades, "strategy").items():
        n = g["trades"]
        out[s] = (g["win_rate"] * n + 1) / (n + 2)
    return out


# --------------------------------------------------------------------- walk-forward
def windows(first: pd.Timestamp, last: pd.Timestamp) -> list[tuple[pd.Timestamp, ...]]:
    """(train start, train end = test start, test end); tests are consecutive and never
    overlap; each training period ends where its test begins."""
    out = []
    t0 = first
    while True:
        train_end = t0 + pd.DateOffset(months=TRAIN_MONTHS)
        if train_end >= last:
            break
        test_end = min(train_end + pd.DateOffset(months=TEST_MONTHS), last)
        out.append((t0, train_end, test_end))
        t0 = t0 + pd.DateOffset(months=TEST_MONTHS)
    return out


_POOL: dict[str, Any] = {}


def _init(data: Data, profile: Profile, carry: dict[str, pd.Series]) -> None:
    _POOL.update(data=data, profile=profile, carry=carry)


def _train_job(args: tuple[str, dict[str, float], pd.Timestamp, pd.Timestamp]
               ) -> tuple[dict[str, float], float | None, int, list[dict[str, Any]]]:
    mode, params, a, b = args
    res = run(_POOL["data"], _POOL["profile"], mode, params, a, b, carry=_POOL["carry"])
    m = metrics(res)
    return params, m.get("expectancy_r"), m["trades"], res["trades"]


class WindowVeto:
    """The ML veto trained for the window a decision falls in (None = no veto)."""

    def __init__(self) -> None:
        self.spans: list[tuple[pd.Timestamp, pd.Timestamp, Any]] = []

    def __call__(self, pos: Position, target_r: float) -> bool:
        for a, b, v in self.spans:
            if a <= pd.Timestamp(pos.opened) < b:
                return bool(v(pos, target_r)) if v is not None else False
        return False


def walk_forward(data: Data, profile: Profile, mode: str, carry: dict[str, pd.Series], *,
                 use_ml: bool = False, workers: int = 4) -> dict[str, Any]:
    """For each window: choose the grid point with the best TRAINING expectancy (at
    least 30 training trades, else the defaults); estimate per-strategy win rates on the
    training trades for the expected-value check; optionally train the ML veto on the
    training trades. The test periods are then traded as ONE continuous book with each
    period's chosen settings, so halts and open positions carry across periods. Also
    returns the same out-of-sample run at 2x costs."""
    first = min(s.index[0] for s, _ in data.values()) + pd.DateOffset(months=WARMUP_MONTHS)
    last = max(s.index[-1] for s, _ in data.values()) + pd.Timedelta(seconds=1)
    grid = [dict(zip(GRID, v, strict=True)) for v in itertools.product(*GRID.values())]
    chosen: list[dict[str, Any]] = []
    frames: dict[str, list[pd.DataFrame]] = {}
    picks: dict[str, list[pd.DataFrame]] = {}
    veto = WindowVeto()
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(data, profile, carry)) as ex:
        for a, b, c in windows(first, last):
            res = list(ex.map(_train_job, [(mode, g, a, b) for g in grid]))
            ok = [x for x in res if x[1] is not None and x[2] >= MIN_TRAIN_TRADES]
            best = max(ok, key=lambda x: float(x[1] or 0.0)) if ok else None
            params = best[0] if best else dict(DEFAULTS)
            train_trades = best[3] if best else []
            model = ml.train(train_trades) if use_ml else None
            veto.spans.append((b, c, ml.make_veto(model) if model is not None else None))
            wr = win_rates(train_trades) if train_trades else None
            fr, pk = frames_for(data, profile, mode, params, b, c, p_win=wr)
            for pair in fr:
                frames.setdefault(pair, []).append(fr[pair])
                picks.setdefault(pair, []).append(pk[pair])
            chosen.append({"train": [str(a.date()), str(b.date())],
                           "test": [str(b.date()), str(c.date())], "params": params,
                           "train_trades": best[2] if best else None,
                           "train_expectancy_r": best[1] if best else None,
                           "win_rates": wr, "ml_veto": model is not None})
    if not frames:
        empty = simulate({}, {}, profile)
        return {"result": empty, "stress": empty, "windows": chosen}
    fs = {k: pd.concat(v) for k, v in frames.items()}
    ps = {k: pd.concat(v) for k, v in picks.items()}
    result = simulate(fs, ps, profile, carry=carry, veto=veto if use_ml else None)
    stress = simulate(fs, ps, profile, carry=carry, cost_mult=2.0,
                      veto=veto if use_ml else None)
    curve = pd.Series([v for _, v in result["curve"]],
                      index=pd.DatetimeIndex([t for t, _ in result["curve"]]))
    for w in chosen:
        seg = curve[(curve.index >= pd.Timestamp(w["test"][0], tz="UTC")) &
                    (curve.index < pd.Timestamp(w["test"][1], tz="UTC"))]
        w["test_return"] = float(seg.iloc[-1] / seg.iloc[0] - 1) if len(seg) > 1 else None
    return {"result": result, "stress": stress, "windows": chosen}


# ------------------------------------------------------------------- robustness
def monte_carlo(r: np.ndarray, *, risk: float = 0.0025, n: int = 5000,
                seed: int = 0) -> dict[str, Any]:
    """Resample the out-of-sample trade R multiples with replacement (same count) and
    compound them at 0.25% risk per R: distribution of the final return and of the
    maximum drawdown. Conservative where exposure caps cut actual size below 0.25%."""
    if len(r) < 10:
        return {"note": "fewer than 10 trades"}
    rng = np.random.default_rng(seed)
    finals, dds = np.empty(n), np.empty(n)
    for i in range(n):
        path = np.cumprod(1 + rng.choice(r, size=len(r), replace=True) * risk)
        path = np.concatenate([[1.0], path])
        finals[i] = path[-1] - 1
        dds[i] = float(np.min(path / np.maximum.accumulate(path) - 1))
    return {"return_p05": float(np.percentile(finals, 5)),
            "return_median": float(np.median(finals)),
            "return_p95": float(np.percentile(finals, 95)),
            "drawdown_median": float(np.median(dds)),
            "drawdown_p95": float(np.percentile(dds, 5)),
            "prob_loss": float((finals < 0).mean()),
            "prob_drawdown_over_5pct": float((dds <= -0.05).mean())}


def sensitivity(data: Data, profile: Profile, start: pd.Timestamp, end: pd.Timestamp,
                carry: dict[str, pd.Series]) -> dict[str, Any]:
    """One parameter at a time at -20% and +20% of its default; adaptive mode."""
    out: dict[str, Any] = {}
    keys = ("expectancy_r", "profit_factor", "sharpe", "net_return", "trades")
    base = metrics(run(data, profile, "adaptive", dict(DEFAULTS), start, end, carry=carry))
    out["default"] = {k: base.get(k) for k in keys}
    for k, v in DEFAULTS.items():
        for mult in (0.8, 1.2):
            p = dict(DEFAULTS) | {k: v * mult}
            m = metrics(run(data, profile, "adaptive", p, start, end, carry=carry))
            out[f"{k} x{mult}"] = {x: m.get(x) for x in keys}
    exps = [x["expectancy_r"] for x in out.values() if x["expectancy_r"] is not None]
    out["share_positive_expectancy"] = float(np.mean([e > 0 for e in exps])) if exps else None
    return out


def gates(m: dict[str, Any], wins: list[float | None], mc: dict[str, Any],
          stress: dict[str, Any], p: float | None, alpha: float) -> dict[str, bool]:
    """The pre-registered pass conditions (all must hold)."""
    w = [x for x in wins if x is not None]
    return {"at_least_100_trades": m["trades"] >= 100,
            "net_positive_after_costs_and_carry": (m.get("net_return") or 0) > 0,
            "profit_factor_ge_1_2": (m.get("profit_factor") or 0) >= 1.2,
            "sharpe_ge_0_5": (m.get("sharpe") or 0) >= 0.5,
            "max_drawdown_within_5pct": m.get("halted") is None and
                (m.get("max_drawdown") or 0) > -0.05,
            "mc_prob_drawdown_over_5pct_le_25pct":
                mc.get("prob_drawdown_over_5pct", 1.0) <= 0.25,
            "positive_in_60pct_of_test_windows": bool(w) and
                float(np.mean([x > 0 for x in w])) >= 0.6,
            "positive_at_2x_costs": (stress.get("net_return") or 0) > 0,
            "significant_after_holm": p is not None and p < alpha}


def evaluate(data: Data, profile: Profile, carry: dict[str, pd.Series], *, workers: int,
             alpha: float = 0.05) -> dict[str, Any]:
    wf = walk_forward(data, profile, "adaptive", carry, workers=workers)
    res = wf["result"]
    m = metrics(res)
    r = np.array([t["r"] for t in res["trades"]])
    p = stationary_bootstrap_pvalue(r, simulations=5000, block=5) if len(r) >= 10 else None
    mc = monte_carlo(r)
    stress = metrics(wf["stress"])
    out: dict[str, Any] = {
        "walk_forward": {"metrics": m, "p_value": p, "monte_carlo": mc,
                         "stress_2x_costs": {k: stress.get(k) for k in
                                             ("net_return", "sharpe", "expectancy_r",
                                              "profit_factor", "max_drawdown", "trades")},
                         "windows": wf["windows"]}}
    out["walk_forward"]["gates"] = gates(m, [w.get("test_return") for w in wf["windows"]],
                                         mc, stress, p, alpha)
    if not wf["windows"]:
        return out
    oos_start = pd.Timestamp(wf["windows"][0]["test"][0], tz="UTC")
    oos_end = max(s.index[-1] for s, _ in data.values()) + pd.Timedelta(seconds=1)
    out["comparison_default_params"] = {
        mode: {k: v for k, v in metrics(run(data, profile, mode, dict(DEFAULTS), oos_start,
                                            oos_end, carry=carry)).items()
               if k not in ("by_year",)} for mode in MODES}
    wf_ml = walk_forward(data, profile, "adaptive", carry, use_ml=True, workers=workers)
    out["adaptive_with_ml_filter"] = {
        k: v for k, v in metrics(wf_ml["result"]).items()
        if k in ("trades", "net_return", "sharpe", "expectancy_r", "profit_factor",
                 "max_drawdown", "win_rate", "rejections")} | {
        "windows_with_model": sum(1 for w in wf_ml["windows"] if w["ml_veto"])}
    out["sensitivity"] = sensitivity(data, profile, oos_start, oos_end, carry)
    return out


def holm_flags(pvalues: dict[str, float | None], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down across the profiles actually run."""
    ps = sorted(((p, k) for k, p in pvalues.items() if p is not None), key=lambda x: x[0])
    out = {k: False for k in pvalues}
    m = len(pvalues)
    for i, (p, k) in enumerate(ps):
        if p >= alpha / (m - i):
            break
        out[k] = True
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--profiles", default="scalping,intraday,swing")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    carry = load_carry(a.data)
    report: dict[str, Any] = {"profiles": {}, "skipped": {}}
    runnable = {}
    for name in a.profiles.split(","):
        profile = PROFILES[name]
        data = load_dir(a.data, profile)
        if not data:
            report["skipped"][name] = f"no {profile.signal_tf} data"
        elif (cov := coverage_months(data)) < MIN_MONTHS:
            report["skipped"][name] = f"{cov:.0f} months of data; {MIN_MONTHS} needed"
        else:
            runnable[name] = (profile, data)
    for name, (profile, data) in runnable.items():
        report["profiles"][name] = {"pairs": list(data), "coverage_months":
                                    round(coverage_months(data), 1)} | evaluate(
            data, profile, carry, workers=a.workers)
    flags = holm_flags({k: v["walk_forward"]["p_value"]
                        for k, v in report["profiles"].items()})
    for k, v in report["profiles"].items():
        v["walk_forward"]["gates"]["significant_after_holm"] = flags[k]
        v["walk_forward"]["passed"] = all(v["walk_forward"]["gates"].values())
    report.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
                  carry_supplied=bool(carry), equity=EQUITY,
                  kind="historical backtest (no orders placed)", paper=False,
                  preregistration="docs/research/2026-11-27-nqs1-preregistration.md")
    a.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: {"passed": v["walk_forward"]["passed"],
                          "gates": v["walk_forward"]["gates"]}
                      for k, v in report["profiles"].items()} | {"skipped": report["skipped"]}))


if __name__ == "__main__":
    main()
