"""Premium on-demand backtests: claim a queued run, execute, store results.

User parameters are validated here against an allow-list with ranges (the
database only bounds their size); arbitrary code is never executed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..logs import log
from ..signals import strategy as strat
from ..signals.ledger import ensure_strategy
from ..signals.series import HIGHER_TIMEFRAME
from ..signals.service import load_candles
from ..signals.strategy import Params
from ..timeframes import tf_delta
from ..validation.dataset import dataset_hash
from ..validation.registry import finish_experiment, start_experiment
from .baselines import buy_and_hold, random_entries
from .engine import run_backtest
from .metrics import compute_metrics

logger = logging.getLogger("nexus.backtest")
MAX_CURVE_POINTS = 500


class UserParams(BaseModel):
    """Custom strategy parameters a Premium user may change (all optional, all bounded)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    min_net_rr: float | None = Field(default=None, ge=1.0, le=5.0)
    stop_atr: float | None = Field(default=None, ge=0.5, le=4.0)
    rsi_low: float | None = Field(default=None, ge=20, le=50)
    rsi_high: float | None = Field(default=None, ge=50, le=80)
    min_volume_ratio: float | None = Field(default=None, ge=0.0, le=3.0)
    entry_window_bars: int | None = Field(default=None, ge=1, le=24)
    horizon_bars: int | None = Field(default=None, ge=4, le=200)

    def apply(self, base: Params) -> Params:
        return replace(base, **self.model_dump(exclude_none=True))


def claim_next(conn: psycopg.Connection) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update public.backtest_runs set status = 'running', started_at = now() where id = ("
            "select id from public.backtest_runs where status = 'queued' order by created_at for update skip locked limit 1) returning *"
        )
        row = cur.fetchone()
    conn.commit()
    return row


def _downsample(curve: list[tuple[datetime, Decimal]]) -> list[list[str]]:
    step = max(1, len(curve) // MAX_CURVE_POINTS)
    pts = curve[::step]
    if pts[-1] != curve[-1]:
        pts.append(curve[-1])
    return [[t.isoformat(), str(e)] for t, e in pts]


def execute(conn: psycopg.Connection, run: dict[str, Any]) -> str:
    """Run one claimed backtest. Returns the final status."""
    try:
        params = UserParams.model_validate(run["params"]).apply(Params())
    except ValidationError as e:
        msg = "invalid parameters: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors(include_input=False))
        conn.execute("update public.backtest_runs set status = 'failed', error = %s, finished_at = now() where id = %s", (msg[:1000], run["id"]))
        conn.commit()
        return "failed"
    tf = run["timeframe"]
    step = tf_delta(tf)
    lookback = params.min_bars + 50
    until = run["period_end"] + step * (params.entry_window_bars + params.horizon_bars + 2)
    span = int((until - run["period_start"]) / step) + lookback
    candles = load_candles(conn, run["pair_id"], tf, until, span)
    htf_tf = HIGHER_TIMEFRAME[tf]
    htf = load_candles(conn, run["pair_id"], htf_tf, until, int(span * step / tf_delta(htf_tf)) + lookback) if htf_tf else None
    strategy_id = ensure_strategy(conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, Params())
    config = {"backtest_run_id": str(run["id"]), "pair_id": run["pair_id"], "timeframe": tf, "params": params.as_dict(),
              "period": [run["period_start"].isoformat(), run["period_end"].isoformat()]}  # fmt: skip
    exp = start_experiment(conn, "backtest", strategy_id, config, dataset_hash(candles), seed=0)
    try:
        if len(candles) < lookback:
            raise ValueError("not enough stored history for this period")
        res = run_backtest(tf, candles, htf, run["period_start"], run["period_end"], params)
        rules = res.assumptions
        metrics = compute_metrics(res.trades, res.equity_curve, Decimal(rules["risk"]["initial_equity"]), run["period_start"], run["period_end"])  # type: ignore[index]
        metrics.update({"skipped": res.skipped, "open_at_end": res.open_at_end, "halted_at": res.halted_at.isoformat() if res.halted_at else None})
        baselines = {
            "no_trade": "0",
            "buy_and_hold": buy_and_hold(candles, run["period_start"], run["period_end"], params.costs),
            "random_entries": random_entries(candles, tf, res.trades, run["period_start"], run["period_end"], params.costs,
                                             params.entry_window_bars, params.horizon_bars, runs=100, seed=0),
        }  # fmt: skip
        with conn.cursor() as cur:
            cur.execute(
                "insert into public.backtest_results (run_id, metrics, equity_curve, baselines, assumptions) values (%s, %s, %s, %s, %s)",
                (run["id"], json.dumps(metrics), json.dumps(_downsample(res.equity_curve)), json.dumps(baselines), json.dumps(res.assumptions, default=str)),
            )
            cur.executemany(
                "insert into public.backtest_trades (run_id, seq, direction, decided_at, closed_at, outcome, entry_price, exit_price, quantity, "
                "net_return, r_multiple, pnl) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(run["id"], t.seq, t.direction, t.decided_at, t.closed_at, t.outcome, t.entry_price, t.exit_price, t.quantity,
                  t.net_return, t.r_multiple, t.pnl) for t in res.trades],
            )  # fmt: skip
            cur.execute("update public.backtest_runs set status = 'succeeded', experiment_id = %s, finished_at = now() where id = %s", (exp, run["id"]))
        conn.commit()
        finish_experiment(conn, exp, metrics)
        log(logger, logging.INFO, "backtest_done", run_id=str(run["id"]), trades=len(res.trades))
        return "succeeded"
    except Exception as e:  # noqa: BLE001 - a failed run is recorded as failed, never left 'running'
        conn.rollback()
        safe = str(e)[:300] if isinstance(e, ValueError) else "internal error"
        conn.execute(
            "update public.backtest_runs set status = 'failed', error = %s, experiment_id = %s, finished_at = now() where id = %s", (safe, exp, run["id"])
        )
        conn.commit()
        finish_experiment(conn, exp, None, error=type(e).__name__)
        log(logger, logging.ERROR, "backtest_failed", run_id=str(run["id"]), error=type(e).__name__)
        return "failed"


def process_one(conn: psycopg.Connection) -> int:
    run = claim_next(conn)
    if run is None:
        return 0
    execute(conn, run)
    return 1
