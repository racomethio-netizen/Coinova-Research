"""Backtest jobs and the experiment registry against real Postgres as `nexus_engine` (synthetic data)."""

from datetime import timedelta

import psycopg
import pytest
from synthetic import aggregate, random_walk
from test_signals_db import insert
from test_strategy import BUY_SEED
from test_validation import planted

from nexus_engine.backtest.jobs import process_one
from nexus_engine.marketdata.ingest import load_pairs, load_source
from nexus_engine.signals import strategy as strat
from nexus_engine.signals.ledger import ensure_strategy
from nexus_engine.signals.strategy import Params
from nexus_engine.validation.registry import finish_experiment, save_validation_report, start_experiment
from nexus_engine.validation.walkforward import WalkForwardConfig, run_walk_forward

pytestmark = pytest.mark.db


@pytest.fixture
def queued(engine_conn: psycopg.Connection, superuser: psycopg.Connection):
    candles = random_walk("1h", 1400, BUY_SEED)
    pair = next(p for p in load_pairs(engine_conn, load_source(engine_conn, "binance_spot").id) if p.symbol == "BTCUSDT")
    insert(superuser, pair.id, "1h", candles)
    insert(superuser, pair.id, "4h", aggregate(candles, "1h", "4h"))
    uid = superuser.execute("insert into auth.users (email) values (gen_random_uuid()::text || '@test.local') returning id").fetchone()[0]

    def queue(params: str = "{}") -> str:
        return str(superuser.execute(
            "insert into public.backtest_runs (requested_by, pair_id, timeframe, params, period_start, period_end) "
            "values (%s, %s, '1h', %s, %s, %s) returning id",
            (uid, pair.id, params, candles[999].close_time, candles[1350].close_time),
        ).fetchone()[0])  # fmt: skip

    yield queue
    superuser.execute("delete from public.backtest_runs")


def test_queued_backtest_runs_and_stores_reproducible_results(engine_conn: psycopg.Connection, queued) -> None:
    run_id = queued('{"min_net_rr": 1.5}')
    assert process_one(engine_conn) == 1
    status, exp = engine_conn.execute("select status, experiment_id from public.backtest_runs where id = %s", (run_id,)).fetchone()
    assert status == "succeeded" and exp is not None
    metrics, curve, baselines = engine_conn.execute(
        "select metrics, equity_curve, baselines from public.backtest_results where run_id = %s", (run_id,)
    ).fetchone()
    assert metrics["insufficient_sample"] is True  # a handful of trades is never presented as a track record
    assert 1 <= len(curve) <= 501 and baselines["no_trade"] == "0"
    trades = engine_conn.execute("select count(*) from public.backtest_trades where run_id = %s", (run_id,)).fetchone()[0]
    assert trades == metrics["signals"]
    e = engine_conn.execute("select status, kind, dataset_hash, lockfile_hash, engine_version from public.experiments where id = %s", (exp,)).fetchone()
    assert e[0] == "succeeded" and e[1] == "backtest" and len(e[2]) == 64 and len(e[3]) == 64 and e[4]
    assert process_one(engine_conn) == 0  # queue empty


def test_invalid_user_parameters_fail_safely(engine_conn: psycopg.Connection, queued) -> None:
    run_id = queued('{"min_net_rr": 99, "__import__": "os"}')
    process_one(engine_conn)
    status, error = engine_conn.execute("select status, error from public.backtest_runs where id = %s", (run_id,)).fetchone()
    assert status == "failed" and "invalid parameters" in error and "min_net_rr" in error


def test_experiment_and_report_registry(engine_conn: psycopg.Connection) -> None:
    sid = ensure_strategy(engine_conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, Params())
    ds = planted(1200)
    exp = start_experiment(engine_conn, "walk_forward", sid, {"model": "logistic"}, "b" * 64, 7)
    report = run_walk_forward(ds, WalkForwardConfig(), evaluate_lockbox=True)
    rid = save_validation_report(engine_conn, exp, sid, ds.t[0], ds.t[-1] + timedelta(hours=1), report, {"costs": "defaults"})
    finish_experiment(engine_conn, exp, {"approvable": report["approvable"]})
    lockbox = engine_conn.execute("select lockbox from public.validation_reports where id = %s", (rid,)).fetchone()[0]
    assert lockbox["evaluated"] is True
    with pytest.raises(psycopg.errors.RaiseException, match="finished experiments are immutable"):
        finish_experiment(engine_conn, exp, {"tampered": True})
    engine_conn.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        engine_conn.execute("delete from public.validation_reports")
    engine_conn.rollback()
