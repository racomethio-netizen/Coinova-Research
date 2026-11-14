"""Walk-forward validation of the AI model with purging, plus promotion gates.

Per window: train on [train_start, split - purge), test on [split, test_end). A test
trade is taken when the calibrated probability implies positive expected value after
costs (using win/loss magnitudes estimated on the TRAINING data); trades on the same
symbol never overlap. Only out-of-sample results count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from nexus_ai.model import EnsembleModel


@dataclass(frozen=True)
class AiGates:
    min_mean_auc: float = 0.53
    min_auc_windows_share: float = 0.7      # windows with AUC > 0.5
    min_brier_skill: float = 0.0
    min_trades: int = 50
    min_expectancy: float = 0.0
    min_profit_factor: float = 1.1
    min_t_stat: float = 1.5
    min_positive_windows: float = 0.55


@dataclass
class AiReport:
    windows: list[dict[str, Any]]
    oos_trades: pd.DataFrame
    stats: dict[str, Any]
    passed: bool
    failures: list[str] = field(default_factory=list)


def select_trades(proba: npt.NDArray[np.float64], frame: pd.DataFrame, *, avg_win: float, avg_loss: float,
                  min_ev: float) -> pd.DataFrame:
    """Non-overlapping trades where p*avg_win + (1-p)*avg_loss > min_ev (per symbol)."""
    ev = proba * avg_win + (1 - proba) * avg_loss
    frame = frame.assign(p=proba, ev=ev)
    taken = []
    for _, g in frame.groupby("symbol", sort=False):
        busy_until = -1
        for row in g.itertuples():
            if row.pos <= busy_until or row.ev <= min_ev:
                continue
            taken.append(row.Index)
            busy_until = row.end_idx
    return frame.loc[taken]


def walk_forward_ai(dataset: pd.DataFrame, features: list[str], *, train_days: int = 270,
                    test_days: int = 60, purge_bars: int = 48, min_ev: float = 0.0005,
                    gates: AiGates | None = None) -> AiReport:
    """``dataset``: rows indexed by (time) with columns features + label, ret, end_idx,
    pos (row position within its symbol), symbol. Sorted by time."""
    gates = gates or AiGates()
    t0, t1 = dataset.index.min(), dataset.index.max()
    split = t0 + pd.Timedelta(days=train_days)
    windows, trades = [], []
    while split + pd.Timedelta(days=test_days) <= t1:
        test_end = split + pd.Timedelta(days=test_days)
        purge = pd.Timedelta(minutes=15 * purge_bars)
        train = dataset[(dataset.index >= split - pd.Timedelta(days=train_days))
                        & (dataset.index < split - purge)]
        test = dataset[(dataset.index >= split) & (dataset.index < test_end)]
        model = EnsembleModel(features=features, purge=purge_bars).fit(train[features],
                                                                       train["label"])
        fit_part = train.iloc[:model.train_info["n_fit"]]
        avg_win = float(fit_part.ret[fit_part.label == 1].mean())
        avg_loss = float(fit_part.ret[fit_part.label == 0].mean())
        p = model.predict_proba(test[features])
        y = test["label"].to_numpy()
        base = model.train_info["base_rate"]
        auc = float(roc_auc_score(y, p)) if len(set(y)) > 1 else float("nan")
        brier = float(brier_score_loss(y, p))
        brier_ref = float(brier_score_loss(y, np.full(len(y), base)))
        sel = select_trades(p, test, avg_win=avg_win, avg_loss=avg_loss, min_ev=min_ev)
        trades.append(sel.assign(window=str(split.date())))
        windows.append({
            "test_start": str(split.date()), "n_test": len(test), "auc": auc,
            "brier": brier, "brier_ref": brier_ref, "base_rate_train": base,
            "test_base_rate": float(y.mean()), "uncond_expectancy": float(test.ret.mean()),
            "trades": len(sel), "expectancy": float(sel.ret.mean()) if len(sel) else None,
            "win_rate": float((sel.ret > 0).mean()) if len(sel) else None,
        })
        split = test_end
    oos = pd.concat(trades) if trades else pd.DataFrame(columns=["ret"])
    stats = _stats(windows, oos)
    failures = _gate(stats, gates)
    return AiReport(windows, oos, stats, not failures, failures)


def _stats(windows: list[dict[str, Any]], oos: pd.DataFrame) -> dict[str, Any]:
    aucs = [w["auc"] for w in windows if not math.isnan(w["auc"])]
    traded = [w for w in windows if w["trades"]]
    gains, losses = oos.ret[oos.ret > 0].sum(), -oos.ret[oos.ret <= 0].sum()
    s: dict[str, Any] = {
        "windows": len(windows),
        "mean_auc": float(np.mean(aucs)) if aucs else None,
        "auc_windows_share": float(np.mean([a > 0.5 for a in aucs])) if aucs else None,
        "brier_skill": float(1 - np.mean([w["brier"] for w in windows])
                             / np.mean([w["brier_ref"] for w in windows])) if windows else None,
        "trades": len(oos),
        "win_rate": float((oos.ret > 0).mean()) if len(oos) else None,
        "expectancy": float(oos.ret.mean()) if len(oos) else None,
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "positive_window_share": (float(np.mean([(w["expectancy"] or 0) > 0 for w in traded]))
                                  if traded else None),
        "uncond_expectancy": float(np.mean([w["uncond_expectancy"] for w in windows]))
        if windows else None,
    }
    s["t_stat"] = (float(oos.ret.mean() / (oos.ret.std() / math.sqrt(len(oos))))
                   if len(oos) > 1 and oos.ret.std() > 0 else None)
    return s


def _gate(s: dict[str, Any], g: AiGates) -> list[str]:
    f = []
    if s["mean_auc"] is None or s["mean_auc"] < g.min_mean_auc:
        f.append(f"mean AUC {s['mean_auc']} < {g.min_mean_auc}")
    if (s["auc_windows_share"] or 0) < g.min_auc_windows_share:
        f.append(f"AUC>0.5 in {s['auc_windows_share']} of windows < {g.min_auc_windows_share}")
    if s["brier_skill"] is None or s["brier_skill"] <= g.min_brier_skill:
        f.append(f"Brier skill {s['brier_skill']} <= {g.min_brier_skill} (not better than base rate)")
    if s["trades"] < g.min_trades:
        f.append(f"OOS trades {s['trades']} < {g.min_trades}")
    if s["expectancy"] is None or s["expectancy"] <= g.min_expectancy:
        f.append(f"OOS expectancy {s['expectancy']} <= {g.min_expectancy}")
    if s["profit_factor"] is None or s["profit_factor"] < g.min_profit_factor:
        f.append(f"OOS profit factor {s['profit_factor']} < {g.min_profit_factor}")
    if s["t_stat"] is None or s["t_stat"] < g.min_t_stat:
        f.append(f"t-stat {s['t_stat']} < {g.min_t_stat}")
    if (s["positive_window_share"] or 0) < g.min_positive_windows:
        f.append(f"positive windows {s['positive_window_share']} < {g.min_positive_windows}")
    return f
