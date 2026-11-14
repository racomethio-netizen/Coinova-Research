"""``python -m nexus_ai.cli train [--exchange binance]``

Builds the dataset from REAL stored candles, runs purged walk-forward validation,
registers the model version with its evaluation, and saves an artifact ONLY if every
promotion gate passes. Rejected models are recorded with their failure reasons.
"""

from __future__ import annotations

import argparse
import json
import subprocess

from nexus_backtest.data import load_frames
from nexus_backtest.metrics import json_safe
from nexus_common.logging import configure_logging, get_logger
from nexus_config import get_settings
from nexus_db import make_engine, make_session_factory, transaction

from nexus_ai import registry
from nexus_ai.dataset import build_dataset
from nexus_ai.features import FEATURE_VERSION, feature_hash
from nexus_ai.model import XGB_PARAMS, EnsembleModel
from nexus_ai.validation import walk_forward_ai

log = get_logger("nexus.ai.cli")
MODEL_NAME = "xgb_triple_barrier_15m"


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],  # noqa: S603,S607
                                       text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def train(exchange: str) -> dict[str, object]:
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    data = load_frames(factory, exchange, settings.trading_pairs, ["15m", "1h", "4h"])
    label_cfg = {"tp_mult": 2.0, "sl_mult": 1.0, "horizon": 48,
                 "fee_rate": float(settings.paper_fee_bps) / 10_000,
                 "slippage_rate": float(settings.paper_slippage_bps) / 10_000}
    ds, features = build_dataset(data, **label_cfg)  # type: ignore[arg-type]
    report = walk_forward_ai(ds, features)
    metrics = json_safe({"summary": report.stats, "windows": report.windows,
                         "labels": label_cfg, "exchange": exchange,
                         "symbols": settings.trading_pairs})
    with transaction(factory) as s:
        row = registry.register(
            s, name=MODEL_NAME, algorithm="xgb_logit_isotonic",
            feature_set={"version": FEATURE_VERSION, "hash": feature_hash(features),
                         "columns": features},
            hyperparameters={"xgb": XGB_PARAMS, "xgb_weight": 0.7, "labels": label_cfg},
            training_start=ds.index.min().to_pydatetime(),
            training_end=ds.index.max().to_pydatetime(), code_version=_git_sha())
        registry.record_evaluation(
            s, row, evaluation_type="walk_forward", period_start=ds.index.min().to_pydatetime(),
            period_end=ds.index.max().to_pydatetime(), metrics=metrics, passed=report.passed,
            failures=report.failures)
        if report.passed:
            final = EnsembleModel(features=features).fit(ds[features], ds["label"])
            registry.save_artifact(s, row, final)
        result = {"model_id": row.id, "version": row.version, "status": row.status,
                  "failures": report.failures, "summary": metrics["summary"]}
    engine.dispose()
    return result


def main() -> None:
    p = argparse.ArgumentParser(prog="nexus-ai")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--exchange", default="binance")
    args = p.parse_args()
    configure_logging(get_settings().log_level, json=False)
    if args.cmd == "train":
        print(json.dumps(train(args.exchange), indent=2, default=str))


if __name__ == "__main__":
    main()
