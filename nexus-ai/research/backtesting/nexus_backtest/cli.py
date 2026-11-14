"""``python -m nexus_backtest.cli validate-strategies [--exchange binance]``
``python -m nexus_backtest.cli backtest --strategy momentum [--start 2026-01-01]``

``validate-strategies`` runs walk-forward validation for each strategy on REAL stored
candles, records a ``backtest_runs`` row per strategy, and updates the strategy row:
enabled only if every gate passed (with the most recent window's parameters); otherwise
disabled with the failure reasons. Nothing is enabled by default.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from nexus_common.logging import configure_logging
from nexus_config import get_settings
from nexus_db import make_engine, make_session_factory, models, transaction
from nexus_strategies import ALL_STRATEGIES
from sqlalchemy import select

from nexus_backtest.data import load_frames, load_specs
from nexus_backtest.engine import BacktestConfig, run_backtest
from nexus_backtest.metrics import compute_metrics, json_safe
from nexus_backtest.signals import CostModel
from nexus_backtest.walkforward import walk_forward

TIMEFRAMES = ["5m", "15m", "1h", "4h"]


def validate_strategies(exchange: str) -> list[dict[str, object]]:
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    data = load_frames(factory, exchange, settings.trading_pairs, TIMEFRAMES)
    fee = float(settings.paper_fee_bps) / 10_000
    slip = float(settings.paper_slippage_bps) / 10_000
    costs = CostModel(fee, slip)
    results = []
    for code, cls in ALL_STRATEGIES.items():
        rep = walk_forward(data, cls, costs=costs)
        latest_params = next((w["params"] for w in reversed(rep.windows) if w["params"]), None)
        idx = data[settings.trading_pairs[0]]["5m"].index
        metrics = json_safe({"summary": rep.stats, "windows": [
            {**w, "train_start": str(w["train_start"]), "test_start": str(w["test_start"]),
             "test_end": str(w["test_end"])} for w in rep.windows]})
        with transaction(factory) as s:
            strat = s.scalar(select(models.Strategy).where(models.Strategy.code == code))
            assert strat is not None
            s.add(models.BacktestRun(
                strategy_id=strat.id, status="succeeded", is_walk_forward=True,
                config={"exchange": exchange, "symbols": settings.trading_pairs,
                        "train_days": 180, "test_days": 60},
                data_start=idx[0].to_pydatetime(), data_end=idx[-1].to_pydatetime(),
                fee_bps=settings.paper_fee_bps, slippage_bps=settings.paper_slippage_bps,
                metrics={**metrics, "passed": rep.passed, "failures": rep.failures},
                completed_at=datetime.now(UTC)))
            strat.is_enabled = rep.passed
            strat.parameters = json_safe({
                "params": latest_params if rep.passed else None,
                "validation": {"passed": rep.passed, "failures": rep.failures,
                               "oos": rep.stats, "validated_at": datetime.now(UTC).isoformat(),
                               "exchange": exchange}})
        results.append({"strategy": code, "passed": rep.passed, "failures": rep.failures,
                        "oos": json_safe(rep.stats)})
    engine.dispose()
    return results


def backtest(exchange: str, strategy: str, start: str | None, end: str | None) -> dict[str, object]:
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    data = load_frames(factory, exchange, settings.trading_pairs, TIMEFRAMES)
    specs = load_specs(factory, exchange, settings.trading_pairs)
    cfg = BacktestConfig(fee_rate=settings.paper_fee_bps / Decimal(10_000),
                         slippage_rate=settings.paper_slippage_bps / Decimal(10_000),
                         risk=settings.risk)
    res = run_backtest(data, [ALL_STRATEGIES[strategy]()], specs, cfg,
                       start=pd.Timestamp(start, tz="UTC") if start else None,
                       end=pd.Timestamp(end, tz="UTC") if end else None)
    engine.dispose()
    out: dict[str, object] = json_safe(compute_metrics(res))
    return out


def main() -> None:
    p = argparse.ArgumentParser(prog="nexus-backtest")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate-strategies")
    v.add_argument("--exchange", default="binance")
    b = sub.add_parser("backtest")
    b.add_argument("--exchange", default="binance")
    b.add_argument("--strategy", required=True, choices=list(ALL_STRATEGIES))
    b.add_argument("--start")
    b.add_argument("--end")
    args = p.parse_args()
    configure_logging(get_settings().log_level, json=False)
    if args.cmd == "validate-strategies":
        out: object = validate_strategies(args.exchange)
    else:
        out = backtest(args.exchange, args.strategy, args.start, args.end)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
