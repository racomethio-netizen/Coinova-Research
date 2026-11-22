"""Phase D walk-forward research (pre-registered in
docs/research/2026-11-23-ml-phase-d-preregistration.md).

Per walk-forward window (train ``train_days``, test the next ``test_days``):

1. Model choice on TRAIN only: for each family, purged k-fold (with embargo) out-of-fold
   predictions -> the expected-value trading rule -> CV net expectancy. The family with
   the highest CV expectancy (>= ``min_cv_trades`` trades) is chosen; if none is positive
   after costs, the window ABSTAINS (no trades).
2. Every family is fitted on the full train window (time-ordered calibration) for the
   model comparison table and the disagreement measure; an Isolation Forest is fitted on
   train features; an HMM regime model is fitted on train observations.
3. TEST: the chosen model trades where expected value after costs > ``min_ev``, the
   families agree (std of probabilities <= ``max_disagreement``) and the input is not out
   of distribution. Trades on a symbol never overlap.
4. Secondary tasks are scored for skill only (never traded): 3-class direction over 16
   bars with a cost-sized neutral band, and volatility expansion.

Only test-window (out-of-sample) results count. Nothing here touches the test window
before the model is fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from nexus_ai.candidates import (
    CalibratedModel,
    OodDetector,
    available_families,
    disagreement,
    make_estimator,
)
from nexus_ai.cv import label_end_times, purged_kfold
from nexus_ai.evaluation import by_group, classification_metrics, trading_metrics
from nexus_ai.hmm_regime import OBS, HmmRegime
from nexus_ai.validation import AiGates, select_trades

EMBARGO = pd.Timedelta(hours=24)
BAR_15M = pd.Timedelta(minutes=15)


def _mean_of(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return float(np.mean(vals)) if vals else None


@dataclass(frozen=True)
class ResearchConfig:
    train_days: int = 270
    test_days: int = 60
    inner_folds: int = 3
    embargo: pd.Timedelta = EMBARGO
    min_ev: float = 0.0005
    max_disagreement: float = 0.10
    min_cv_trades: int = 30
    bar: pd.Timedelta = BAR_15M
    families: tuple[str, ...] = field(default_factory=lambda: tuple(available_families()))


def _win_loss(frame: pd.DataFrame) -> tuple[float, float]:
    return (float(frame.ret[frame.label == 1].mean()), float(frame.ret[frame.label == 0].mean()))


def _cv_expectancy(train: pd.DataFrame, ends: pd.Series, features: list[str], family: str,
                   cfg: ResearchConfig) -> dict[str, Any]:
    rets: list[pd.Series] = []
    for tr_idx, te_idx in purged_kfold(train.index, ends, cfg.inner_folds, cfg.embargo):
        tr, te = train.iloc[tr_idx], train.iloc[te_idx]
        try:
            m = CalibratedModel(family, features).fit(tr[features], tr["label"], ends.iloc[tr_idx])
        except ValueError:
            continue
        w, lo = _win_loss(tr)
        sel = select_trades(m.predict_proba(te[features]), te, avg_win=w, avg_loss=lo,
                            min_ev=cfg.min_ev)
        rets.append(sel["ret"])
    r = pd.concat(rets) if rets else pd.Series(dtype=float)
    return {"trades": len(r), "expectancy": float(r.mean()) if len(r) else None}


def _hmm_fit(train: pd.DataFrame) -> HmmRegime:
    groups = [g for _s, g in train.groupby("symbol", sort=True)]
    return HmmRegime().fit(pd.concat(groups), [len(g) for g in groups])


def _hmm_states(ds: pd.DataFrame, hmm: HmmRegime) -> dict[str, pd.Series]:
    """Forward-filtered state per symbol (filtering runs over train + test so the test
    period starts from the right prior; each row still uses only earlier rows)."""
    return {str(sym): hmm.filter(g)["hmm_state"] for sym, g in ds.groupby("symbol", sort=True)}


def walk_forward_v2(ds: pd.DataFrame, features: list[str], cfg: ResearchConfig | None = None,
                    gates: AiGates | None = None) -> dict[str, Any]:
    cfg = cfg or ResearchConfig()
    gates = gates or AiGates()
    ends = label_end_times(ds, cfg.bar)
    t0, t1 = ds.index.min(), ds.index.max()
    split = t0 + pd.Timedelta(days=cfg.train_days)
    windows: list[dict[str, Any]] = []
    system_trades: list[pd.DataFrame] = []
    family_trades: dict[str, list[pd.DataFrame]] = {f: [] for f in cfg.families}
    family_cls: dict[str, list[dict[str, Any]]] = {f: [] for f in cfg.families}
    aux: list[dict[str, Any]] = []
    while split + pd.Timedelta(days=cfg.test_days) <= t1:
        test_end = split + pd.Timedelta(days=cfg.test_days)
        in_train = (ds.index >= split - pd.Timedelta(days=cfg.train_days)) & (ends < split)
        train, tr_ends = ds[in_train], ends[in_train]
        test = ds[(ds.index >= split) & (ds.index < test_end)]
        # 1. choice on train only
        cv = {f: _cv_expectancy(train, tr_ends, features, f, cfg) for f in cfg.families}
        eligible = {f: v for f, v in cv.items()
                    if v["expectancy"] is not None and v["trades"] >= cfg.min_cv_trades}
        chosen = max(eligible, key=lambda f: eligible[f]["expectancy"]) if eligible else None
        if chosen is not None and eligible[chosen]["expectancy"] <= 0:
            chosen = None                                 # nothing positive after costs
        # 2. full fits
        models = {f: CalibratedModel(f, features).fit(train[features], train["label"], tr_ends)
                  for f in cfg.families}
        w, lo = _win_loss(train)
        probs = {f: m.predict_proba(test[features]) for f, m in models.items()}
        spread = disagreement(list(probs.values()))
        ood = OodDetector(features).fit(train).flag(test)
        hmm = _hmm_fit(train)
        both = ds[(ds.index >= split - pd.Timedelta(days=cfg.train_days)) & (ds.index < test_end)]
        states = _hmm_states(both, hmm)
        y = test["label"].to_numpy(float)
        for f in cfg.families:
            family_cls[f].append(classification_metrics(y, probs[f], models[f].info["base_rate"]))
            family_trades[f].append(select_trades(probs[f], test, avg_win=w, avg_loss=lo,
                                                  min_ev=cfg.min_ev).assign(window=str(split.date())))
        # 3. the system's trades
        record: dict[str, Any] = {"test_start": str(split.date()), "n_test": len(test),
                                  "cv": cv, "chosen": chosen,
                                  "ood_share": float(ood.mean()),
                                  "high_disagreement_share": float((spread > cfg.max_disagreement).mean()),
                                  "test_base_rate": float(y.mean()),
                                  "uncond_expectancy": float(test.ret.mean())}
        if chosen is not None:
            ok = (~ood) & (spread <= cfg.max_disagreement)
            sel = select_trades(np.where(ok, probs[chosen], 0.0), test, avg_win=w, avg_loss=lo,
                                min_ev=cfg.min_ev)
            hmm_col = [states[str(sym)].get(ts, "n/a") for ts, sym in zip(sel.index, sel["symbol"],
                                                                        strict=True)]
            sel = sel.assign(window=str(split.date()), family=chosen, hmm_state=hmm_col)
            system_trades.append(sel)
            record["auc"] = family_cls[chosen][-1]["roc_auc"]
            record["brier_skill"] = family_cls[chosen][-1]["brier_skill"]
            record["trades"] = len(sel)
            record["expectancy"] = float(sel.ret.mean()) if len(sel) else None
        else:
            record.update(auc=None, brier_skill=None, trades=0, expectancy=None)
        aux.append(_aux_tasks(train, test, features))
        windows.append(record)
        split = test_end
    return _summarize(windows, system_trades, family_trades, family_cls, aux, cfg, gates)


def _aux_tasks(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    """Skill of the secondary tasks (never traded)."""
    from sklearn.metrics import log_loss, roc_auc_score

    out: dict[str, Any] = {}
    fam = "lgbm" if "lgbm" in available_families() else "xgb"
    med = train[features].median()
    Xtr, Xte = train[features].fillna(med).fillna(0), test[features].fillna(med).fillna(0)
    tr = train["direction_16"].notna()
    te = test["direction_16"].notna()
    if tr.sum() > 1000 and te.sum() > 100:
        from lightgbm import LGBMClassifier

        clf = LGBMClassifier(n_estimators=200, num_leaves=15, learning_rate=0.03,
                             min_child_samples=100, random_state=7, verbose=-1, n_jobs=4)
        ytr = train.loc[tr, "direction_16"].astype(int) + 1
        yte = test.loc[te, "direction_16"].astype(int) + 1
        clf.fit(Xtr[tr], ytr)
        p = np.asarray(clf.predict_proba(Xte[te]))
        prior = np.tile(np.bincount(ytr, minlength=3) / len(ytr), (len(yte), 1))
        out["direction_logloss"] = float(log_loss(yte, p, labels=[0, 1, 2]))
        out["direction_logloss_prior"] = float(log_loss(yte, prior, labels=[0, 1, 2]))
        out["direction_accuracy"] = float((p.argmax(1) == yte.to_numpy()).mean())
        out["direction_majority"] = float((yte == np.bincount(ytr).argmax()).mean())
    tr = train["vol_expansion"].notna()
    te = test["vol_expansion"].notna()
    if tr.sum() > 1000 and te.sum() > 100 and test.loc[te, "vol_expansion"].nunique() > 1:
        est = make_estimator(fam).fit(Xtr[tr], train.loc[tr, "vol_expansion"].astype(int))
        out["vol_expansion_auc"] = float(roc_auc_score(test.loc[te, "vol_expansion"],
                                                       est.predict_proba(Xte[te])[:, 1]))
    return out


def _summarize(windows: list[dict[str, Any]], system_trades: list[pd.DataFrame],
               family_trades: dict[str, list[pd.DataFrame]],
               family_cls: dict[str, list[dict[str, Any]]], aux: list[dict[str, Any]],
               cfg: ResearchConfig, gates: AiGates) -> dict[str, Any]:
    oos = pd.concat(system_trades) if system_trades else pd.DataFrame(columns=["ret"])
    days = len(windows) * cfg.test_days
    tpy = len(oos) / days * 365 if days and len(oos) else None
    system = trading_metrics(oos["ret"], trades_per_year=tpy) if len(oos) else trading_metrics(
        pd.Series(dtype=float))
    chosen_aucs = [w["auc"] for w in windows if w.get("auc") is not None]
    traded = [w for w in windows if w["trades"]]
    stats = {
        **system,
        "windows": len(windows), "windows_abstained": sum(1 for w in windows if w["chosen"] is None),
        "mean_auc": float(np.mean(chosen_aucs)) if chosen_aucs else None,
        "auc_windows_share": float(np.mean([a > 0.5 for a in chosen_aucs])) if chosen_aucs else None,
        "brier_skill": _mean_of(windows, "brier_skill"),
        "positive_window_share": (float(np.mean([(w["expectancy"] or 0) > 0 for w in traded]))
                                  if traded else None),
        "uncond_expectancy": float(np.mean([w["uncond_expectancy"] for w in windows])) if windows else None,
    }
    failures = []
    if stats["mean_auc"] is None or stats["mean_auc"] < gates.min_mean_auc:
        failures.append(f"mean AUC {stats['mean_auc']} < {gates.min_mean_auc}")
    if (stats["auc_windows_share"] or 0) < gates.min_auc_windows_share:
        failures.append(f"AUC > 0.5 in {stats['auc_windows_share']} of windows")
    if stats["brier_skill"] is None or stats["brier_skill"] <= gates.min_brier_skill:
        failures.append(f"Brier skill {stats['brier_skill']} <= {gates.min_brier_skill}")
    if stats["trades"] < gates.min_trades:
        failures.append(f"OOS trades {stats['trades']} < {gates.min_trades}")
    if stats["expectancy"] is None or stats["expectancy"] <= gates.min_expectancy:
        failures.append(f"OOS expectancy {stats['expectancy']} <= {gates.min_expectancy}")
    if stats["profit_factor"] is None or stats["profit_factor"] < gates.min_profit_factor:
        failures.append(f"OOS profit factor {stats['profit_factor']} < {gates.min_profit_factor}")
    if stats["t_stat"] is None or stats["t_stat"] < gates.min_t_stat:
        failures.append(f"t-stat {stats['t_stat']} < {gates.min_t_stat}")
    if (stats["positive_window_share"] or 0) < gates.min_positive_windows:
        failures.append(f"positive windows {stats['positive_window_share']} < {gates.min_positive_windows}")
    comparison = {}
    for f in cfg.families:
        ft = pd.concat(family_trades[f]) if family_trades[f] else pd.DataFrame(columns=["ret"])
        cls = family_cls[f]
        comparison[f] = {
            "mean_auc": _mean_of(cls, "roc_auc"), "mean_pr_auc": _mean_of(cls, "pr_auc"),
            "mean_brier_skill": _mean_of(cls, "brier_skill"), "mean_ece": _mean_of(cls, "ece"),
            "mean_precision": _mean_of(cls, "precision"), "mean_recall": _mean_of(cls, "recall"),
            "mean_f1": _mean_of(cls, "f1"),
            "trading_without_filters": trading_metrics(ft["ret"]),
        }
    aux_summary = {k: float(np.mean([a[k] for a in aux if k in a]))
                   for k in ("direction_logloss", "direction_logloss_prior", "direction_accuracy",
                             "direction_majority", "vol_expansion_auc")
                   if any(k in a for a in aux)}
    return {
        "passed": not failures, "failures": failures, "system": stats,
        "by_asset": by_group(oos, "symbol") if len(oos) else {},
        "by_hmm_state": by_group(oos, "hmm_state") if len(oos) and "hmm_state" in oos else {},
        "by_family": by_group(oos, "family") if len(oos) and "family" in oos else {},
        "model_comparison": comparison, "secondary_tasks": aux_summary, "windows": windows,
        "config": {"train_days": cfg.train_days, "test_days": cfg.test_days,
                   "inner_folds": cfg.inner_folds, "embargo": str(cfg.embargo),
                   "min_ev": cfg.min_ev, "max_disagreement": cfg.max_disagreement,
                   "families": list(cfg.families)},
        "observation_columns_hmm": list(OBS),
    }
