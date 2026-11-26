"""Market-neutral statistical model on all Binance USDT perpetuals ("Medallion-style").

Pre-registration: docs/research/2026-11-26-statarb-ml-preregistration.md. Point-in-time
universe (top 60 by 30-day quote volume, delisted coins included), 16 cross-sectionally
ranked features, monthly walk-forward Ridge and XGBoost, daily long-10 / short-10 at 1x
gross with taker fee, slippage and actual funding.

Usage: ``python -m nexus_backtest.statarb_ml --klines D --funding D --out F``
"""

from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.batch3 import verify_checksums
from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

OOS_START = pd.Timestamp("2022-01-01", tz="UTC")
OOS_END = pd.Timestamp("2026-11-01", tz="UTC")
TOP_N, MIN_BARS, N_SIDE, W_SIDE = 60, 60, 10, 0.05
COST = 0.0005 + 0.0005                       # taker fee + slippage, per side
TRAIN_DAYS = 365
STABLE_STD = 0.002
EXCLUDED_BASES = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "USD1",
                  "XAU", "XAG", "XAUT", "PAXG", "BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD"}
FEATURES = ("r1", "r3", "r7", "r14", "r30", "vol_shock", "rvol14", "range1", "close_loc",
            "dist_high30", "dist_low30", "fund3", "fund_chg", "beta60", "resid7", "size")
RIDGE_ALPHA = 10.0
XGB_PARAMS = {"max_depth": 3, "n_estimators": 300, "learning_rate": 0.05,
              "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 50,
              "random_state": 0, "n_jobs": 4, "verbosity": 0}


# ------------------------------------------------------------------------------ data
def _csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(df.iloc[0, 0]).strip().isdigit():
        df = pd.read_csv(io.BytesIO(raw))
        df.columns = range(df.shape[1])
    return df


def _ts(ms: pd.Series) -> pd.DatetimeIndex:
    t = ms.to_numpy().astype("int64")
    t = np.where(t > 10**14, t // 1000, t)
    return pd.to_datetime(t, unit="ms", utc=True)


def load_panel(kdir: Path, fdir: Path) -> dict[str, pd.DataFrame]:
    """Wide daily frames (date x symbol): close, high, low, qvol, funding (sum of the
    settlements during each day, i.e. in (d, d + 1])."""
    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in ("close", "high", "low", "qvol")}
    by_sym: dict[str, list[Path]] = {}
    for z in sorted(kdir.glob("*-1d-*.zip")):
        by_sym.setdefault(z.name.split("-1d-")[0], []).append(z)
    for sym, files in by_sym.items():
        parts = []
        for z in files:
            with zipfile.ZipFile(z) as zf:
                parts.append(_csv(zf.read(zf.namelist()[0])).iloc[:, [0, 2, 3, 4, 7]])
        df = pd.concat(parts).astype(float)
        df.index = _ts(df[0]).normalize()
        df = df[~df.index.duplicated()].sort_index()
        for key, c in (("high", 2), ("low", 3), ("close", 4), ("qvol", 7)):
            cols[key][sym] = df[c]
    out = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    fund: dict[str, pd.Series] = {}
    for sym in out["close"].columns:
        rows = []
        for z in sorted(fdir.glob(f"{sym}-fundingRate-*.zip")):
            with zipfile.ZipFile(z) as zf:
                rows.append(_csv(zf.read(zf.namelist()[0])).iloc[:, [0, 2]].astype(float))
        if rows:
            f = pd.concat(rows)
            ts = _ts(f[0]) - pd.Timedelta(milliseconds=1)
            fund[sym] = pd.Series(f[2].to_numpy(), index=ts.floor("D")).groupby(level=0).sum()
    out["funding"] = pd.DataFrame(fund).reindex(index=out["close"].index,
                                                columns=out["close"].columns).fillna(0.0)
    return out


# -------------------------------------------------------------------------- features
def xs_rank(df: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional rank within the universe, centred to [-0.5, 0.5]; a missing value
    inside the universe becomes 0 (the middle)."""
    x = df.where(mask)
    r = x.rank(axis=1, method="average")
    m = x.notna().sum(axis=1)
    centred = (r - 1).div((m - 1).clip(lower=1), axis=0) - 0.5
    return centred.where(mask).fillna(0.0).where(mask)


def build(panel: dict[str, pd.DataFrame], btc: str = "BTCUSDT"
          ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Features (row d = information up to the close of d-1), the target (rank of day d's
    return), the universe mask, and raw day-d returns."""
    c, hi, lo, qv, fu = (panel[k] for k in ("close", "high", "low", "qvol", "funding"))
    ret = c / c.shift(1) - 1
    bases = pd.Index([s[:-4] for s in c.columns])
    allowed = ~bases.isin(list(EXCLUDED_BASES))
    bars = c.notna().cumsum()
    std60 = ret.rolling(60, min_periods=30).std()
    qv30 = qv.rolling(30, min_periods=20).mean()
    # Known at the start of day d (everything computed on bars <= d-1, then shifted).
    known = (bars >= MIN_BARS) & c.notna() & (std60 >= STABLE_STD) & qv30.notna()
    known = known.loc[:, allowed]
    known = known.reindex(columns=c.columns, fill_value=False).shift(1, fill_value=False)
    size_prev = qv30.shift(1).where(known)
    top = size_prev.rank(axis=1, ascending=False, method="first") <= TOP_N
    universe = known & top
    rb = ret[btc] if btc in ret else ret.mean(axis=1)
    cov = (ret.mul(rb, axis=0)).rolling(60, min_periods=30).mean() - \
        ret.rolling(60, min_periods=30).mean().mul(rb.rolling(60, min_periods=30).mean(),
                                                    axis=0)
    beta = cov.div(rb.rolling(60, min_periods=30).var(ddof=0), axis=0)
    r7 = c / c.shift(7) - 1
    raw = {
        "r1": ret, "r3": c / c.shift(3) - 1, "r7": r7, "r14": c / c.shift(14) - 1,
        "r30": c / c.shift(30) - 1,
        "vol_shock": np.log(qv / qv30.replace(0, np.nan)),
        "rvol14": ret.rolling(14, min_periods=10).std(),
        "range1": (hi - lo) / c,
        "close_loc": (c - lo) / (hi - lo).replace(0, np.nan),
        "dist_high30": c / hi.rolling(30, min_periods=20).max() - 1,
        "dist_low30": c / lo.rolling(30, min_periods=20).min() - 1,
        "fund3": fu.rolling(3, min_periods=1).mean(),
        "fund_chg": fu.rolling(3, min_periods=1).mean() -
        fu.shift(3).rolling(7, min_periods=1).mean(),
        "beta60": beta,
        "resid7": r7 - beta.mul(rb.add(1).rolling(7).apply(np.prod, raw=True) - 1, axis=0),
        "size": np.log(qv30.replace(0, np.nan)),
    }
    feats = {k: xs_rank(v.shift(1), universe) for k, v in raw.items()}
    target = xs_rank(ret, universe & ret.notna())
    return feats, target, universe, ret


def long_frame(feats: dict[str, pd.DataFrame], target: pd.DataFrame,
               universe: pd.DataFrame) -> pd.DataFrame:
    stacked = {k: v.stack() for k, v in feats.items()}
    df = pd.DataFrame(stacked)
    df["target"] = target.stack().reindex(df.index)
    idx = universe.stack()
    df = df.loc[df.index.intersection(idx[idx].index)]
    df.index.names = ["date", "symbol"]
    return df.sort_index()


# ---------------------------------------------------------------------------- models
def walk_forward(df: pd.DataFrame, model: str, start: pd.Timestamp = OOS_START,
                 end: pd.Timestamp = OOS_END) -> tuple[pd.Series, dict[str, float]]:
    """Monthly retrain on the previous 365 days (targets known before the month);
    returns predictions for every coin-day of the test window and mean importances."""
    from sklearn.linear_model import Ridge
    from xgboost import XGBRegressor

    preds = []
    imp: dict[str, list[float]] = {f: [] for f in FEATURES}
    dates = df.index.get_level_values("date")
    for m in pd.date_range(start, end - pd.Timedelta(days=1), freq="MS", tz="UTC"):
        nxt = m + pd.offsets.MonthBegin(1)
        train = df[(dates >= m - pd.Timedelta(days=TRAIN_DAYS)) & (dates < m)].dropna(
            subset=["target"])
        test = df[(dates >= m) & (dates < min(nxt, end))]
        if len(train) < 1000 or test.empty:
            continue
        x, y = train[list(FEATURES)].to_numpy(), train["target"].to_numpy()
        if model == "ridge":
            mdl: Any = Ridge(alpha=RIDGE_ALPHA).fit(x, y)
            for f, w in zip(FEATURES, mdl.coef_, strict=True):
                imp[f].append(float(w))
        else:
            mdl = XGBRegressor(**XGB_PARAMS).fit(x, y)
            for f, w in zip(FEATURES, mdl.feature_importances_, strict=True):
                imp[f].append(float(w))
        preds.append(pd.Series(mdl.predict(test[list(FEATURES)].to_numpy()),
                               index=test.index))
    pred = pd.concat(preds) if preds else pd.Series(dtype=float)
    return pred, {f: float(np.mean(v)) if v else 0.0 for f, v in imp.items()}


# ------------------------------------------------------------------------- portfolio
def portfolio(scores: pd.Series, ret: pd.DataFrame, funding: pd.DataFrame, *,
              cost: float = COST) -> dict[str, Any]:
    """Daily long-top-10 / short-bottom-10 at 5% each, rebalanced to target, costs on
    the traded weight change, actual funding (longs pay positive rates). A coin with no
    bar on a day returns 0 that day (closed at its last close)."""
    s = scores.unstack("symbol").reindex(columns=ret.columns)
    days = s.index
    r_all = ret.reindex(index=days).fillna(0.0).to_numpy()
    f_all = funding.reindex(index=days, columns=ret.columns).fillna(0.0).to_numpy()
    sc = s.to_numpy()
    n = sc.shape[1]
    prev = np.zeros(n)
    contrib = np.zeros(n)
    out_r, out_cost, out_fund, long_pnl, short_pnl, turn = [], [], [], [], [], []
    for i in range(len(days)):
        row = sc[i]
        ok = np.flatnonzero(~np.isnan(row))
        w = np.zeros(n)
        if len(ok) >= 2 * N_SIDE:
            order = ok[np.argsort(row[ok], kind="stable")]
            w[order[-N_SIDE:]] = W_SIDE
            w[order[:N_SIDE]] = -W_SIDE
        traded = float(np.abs(w - prev).sum())
        r, f = r_all[i], f_all[i]
        pnl_i = w * r - w * f
        c = traded * cost
        out_r.append(float(pnl_i.sum()) - c)
        out_cost.append(c)
        out_fund.append(float(-(w * f).sum()))
        long_pnl.append(float(pnl_i[w > 0].sum()))
        short_pnl.append(float(pnl_i[w < 0].sum()))
        turn.append(traded)
        contrib += pnl_i
        gross = 1 + float((w * r).sum())
        prev = w * (1 + r) / gross if gross > 0 else np.zeros(n)
    return {"daily": pd.Series(out_r, index=days), "costs": float(sum(out_cost)),
            "funding": float(sum(out_fund)), "long_pnl": float(sum(long_pnl)),
            "short_pnl": float(sum(short_pnl)),
            "avg_turnover": float(np.mean(turn)) if turn else 0.0,
            "contrib": pd.Series(contrib, index=ret.columns)}


def stats(daily: pd.Series) -> dict[str, Any]:
    eq = (1 + daily).cumprod()
    sd = float(daily.std(ddof=1))
    return {"net_return": float(eq.iloc[-1] - 1) if len(eq) else 0.0,
            "sharpe": float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0,
            "by_year": {str(y): float((1 + g).prod() - 1)
                        for y, g in daily.groupby(daily.index.year)},
            "days": len(daily)}


def evaluate(scores: pd.Series, ret: pd.DataFrame, funding: pd.DataFrame) -> dict[str, Any]:
    base = portfolio(scores, ret, funding)
    stress = portfolio(scores, ret, funding, cost=2 * COST)
    st = stats(base["daily"])
    top3 = base["contrib"].sort_values(ascending=False).head(3)
    return {**st, "stress_2x": stats(stress["daily"]), "costs": base["costs"],
            "funding": base["funding"], "long_pnl_sum": base["long_pnl"],
            "short_pnl_sum": base["short_pnl"], "avg_turnover": base["avg_turnover"],
            "sum_daily_returns": float(base["daily"].sum()),
            "top3_contributors": {k: float(v) for k, v in top3.items()},
            "sum_without_top3": float(base["daily"].sum() - top3.sum()),
            "_daily": base["daily"]}


def report(results: dict[str, dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    pvals = {k: stationary_bootstrap_pvalue(v["_daily"].to_numpy(), simulations=5000,
                                            block=10) for k, v in results.items()}
    sig = holm(pvals)
    out: dict[str, Any] = {}
    for k, v in results.items():
        g = {"net_positive": v["net_return"] > 0,
             "sharpe_ge_1": (v["sharpe"] or 0) >= 1.0,
             "significant_holm": bool(sig[k]),
             "drawdown_le_25pct": v["max_drawdown"] >= -0.25,
             "positive_4_of_5_years": sum(1 for x in v["by_year"].values() if x > 0) >= 4,
             "positive_at_2x_costs": v["stress_2x"]["net_return"] > 0,
             "beats_reversal_baseline": (v["sharpe"] or -9) > (baseline["sharpe"] or -9),
             "positive_without_top3": v["sum_without_top3"] > 0}
        out[k] = {**{x: y for x, y in v.items() if not x.startswith("_")},
                  "p_value": pvals[k], "gates": g, "passed": all(g.values())}
    return {"models": out,
            "baseline_reversal_1d": {x: y for x, y in baseline.items()
                                     if not x.startswith("_")},
            "summary": {"passed": [k for k, v in out.items() if v["passed"]]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", type=Path, required=True)
    ap.add_argument("--funding", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    checks = {"klines": verify_checksums(a.klines), "funding": verify_checksums(a.funding)}
    for name, chk in checks.items():
        if chk["mismatched"] or chk["no_checksum"]:
            raise SystemExit(f"{name}: checksum problems {chk}")
    panel = load_panel(a.klines, a.funding)
    feats, target, universe, ret = build(panel)
    df = long_frame(feats, target, universe)
    results, importances = {}, {}
    for model in ("ridge", "xgboost"):
        pred, imp = walk_forward(df, model)
        results[model] = evaluate(pred, ret, panel["funding"])
        importances[model] = imp
    oos = df[(df.index.get_level_values("date") >= OOS_START) &
             (df.index.get_level_values("date") < OOS_END)]
    baseline = evaluate(-oos["r1"], ret, panel["funding"])
    rep = report(results, baseline)
    per_day = universe.loc[(universe.index >= OOS_START) & (universe.index < OOS_END)].sum(1)
    rep.update(importances=importances, generated_at=datetime.now(UTC).isoformat(),
               harness_commit=a.commit, kind="historical backtest (no orders placed)",
               paper=False, window=[str(OOS_START.date()), "2026-10-31"],
               preregistration="docs/research/2026-11-26-statarb-ml-preregistration.md",
               data={"symbols": int(panel["close"].shape[1]),
                     "eligible_per_day": {"min": int(per_day.min()),
                                          "mean": float(per_day.mean()),
                                          "max": int(per_day.max())},
                     "archives": {k: v["verified"] for k, v in checks.items()}})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: (v["passed"], v["net_return"], v["sharpe"],
                          [g for g, ok in v["gates"].items() if not ok])
                      for k, v in rep["models"].items()}, default=str))


if __name__ == "__main__":
    main()
