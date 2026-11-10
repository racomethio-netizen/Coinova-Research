"""Walk-forward evaluation producing a validation report (docs/nexus/SIGNAL_ENGINE.md §6).

Decision rule (same as production would use): take a candidate only if the
calibrated probability p >= p_min AND expected value after costs is positive,
i.e. p > 1 / (1 + net_rr). Baselines on the same test samples: the rules-only
strategy (net_rr >= the strategy minimum) and all candidates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import numpy as np

from .dataset import FEATURES, Dataset
from .models import auc, brier, fit_calibrator, fit_model, predict, reliability
from .splits import walk_forward

MIN_SAMPLE = 100


@dataclass(frozen=True)
class WalkForwardConfig:
    model: str = "logistic"
    n_folds: int = 4
    lockbox_frac: float = 0.2
    calib_frac: float = 0.2
    embargo_hours: int = 0
    p_min: float = 0.55
    rules_min_rr: float = 1.5
    seed: int = 7


def _trading(sel: np.ndarray, ds: Dataset) -> dict[str, Any]:
    r = ds.net_return[sel]
    n = int(sel.sum())
    return {
        "trades": n,
        "insufficient_sample": n < MIN_SAMPLE,
        "win_rate": None if n == 0 else round(float((r > 0).mean()), 6),
        "mean_net_return": None if n == 0 else round(float(r.mean()), 8),
        "sum_net_return": round(float(r.sum()), 8),
    }


def _evaluate_split(ds: Dataset, fit_idx: np.ndarray, calib_idx: np.ndarray, test_idx: np.ndarray, cfg: WalkForwardConfig) -> dict[str, Any] | None:
    fit, calib, test = ds.take(fit_idx), ds.take(calib_idx), ds.take(test_idx)
    if len(fit) < 10 or len(np.unique(fit.y)) < 2:
        return None
    model = fit_model(cfg.model, fit.X, fit.y, cfg.seed)
    method, cal = fit_calibrator(predict(model, calib.X), calib.y) if len(calib) else ("none", lambda p: p)
    p = cal(predict(model, test.X))
    ece, bins = reliability(p, test.y)
    take = (p >= cfg.p_min) & (p > 1 / (1 + np.maximum(test.net_rr, 1e-9)))
    return {
        "n_fit": len(fit), "n_calib": len(calib), "n_test": len(test), "calibration_method": method,
        "brier": round(brier(p, test.y), 6), "ece": round(ece, 6), "auc": None if (a := auc(p, test.y)) is None else round(a, 6),
        "reliability": bins,
        "model": _trading(take, test),
        "rules_only": _trading(test.net_rr >= cfg.rules_min_rr, test),
        "all_candidates": _trading(np.ones(len(test), dtype=bool), test),
        "_p": p.tolist(), "_y": test.y.tolist(), "_take": take.tolist(),
    }  # fmt: skip


def run_walk_forward(ds: Dataset, cfg: WalkForwardConfig, evaluate_lockbox: bool = False) -> dict[str, Any]:
    folds, lockbox, lock_start = walk_forward(list(ds.t), list(ds.t_end), cfg.n_folds, cfg.lockbox_frac, cfg.calib_frac, timedelta(hours=cfg.embargo_hours))
    fold_reports = []
    all_p: list[float] = []
    all_y: list[int] = []
    for f in folds:
        rep = _evaluate_split(ds, f.fit, f.calib, f.test, cfg)
        if rep is None:
            fold_reports.append({"test_start": f.test_start.isoformat(), "test_end": f.test_end.isoformat(), "status": "insufficient_training_data"})
            continue
        all_p += rep.pop("_p")
        all_y += rep.pop("_y")
        rep.pop("_take")
        fold_reports.append({"test_start": f.test_start.isoformat(), "test_end": f.test_end.isoformat(), "status": "ok", **rep})
    oos: dict[str, Any] = {"samples": len(all_y)}
    if all_y:
        p, y = np.array(all_p), np.array(all_y)
        ece, bins = reliability(p, y)
        oos.update({"brier": round(brier(p, y), 6), "ece": round(ece, 6), "auc": None if (a := auc(p, y)) is None else round(a, 6), "reliability": bins})
    lockbox_report = None
    if evaluate_lockbox and len(lockbox) and lock_start is not None:
        dev = np.array([i for i in range(len(ds)) if ds.t_end[i] < lock_start], dtype=np.int64)
        cut = int(len(dev) * (1 - cfg.calib_frac))
        lockbox_report = _evaluate_split(ds, dev[:cut], dev[cut:], lockbox, cfg)
        if lockbox_report:
            for k in ("_p", "_y", "_take"):
                lockbox_report.pop(k)
    return {
        "config": asdict(cfg), "features": list(FEATURES), "samples": len(ds), "positives": int(ds.y.sum()),
        "folds": fold_reports, "out_of_sample": oos,
        "lockbox": {"start": lock_start.isoformat() if lock_start else None, "samples": len(lockbox),
                    "evaluated": lockbox_report is not None, "result": lockbox_report},
        "approvable": bool(lockbox_report and not lockbox_report["model"]["insufficient_sample"]
                           and (lockbox_report["model"]["mean_net_return"] or 0) > (lockbox_report["rules_only"]["mean_net_return"] or 0)),
    }  # fmt: skip
