"""Database access for strategies, evaluations, the signal ledger and outcomes.

Runs as `nexus_engine`, which may only INSERT into ledger tables (migration 0013).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .execution import RESOLVER_VERSION, Resolution
from .hashing import HASHED_FIELDS, content_hash
from .strategy import Decision, Params


class StrategyChangedError(RuntimeError):
    """Parameters differ from the stored version: bump the strategy version instead."""


def ensure_strategy(conn: psycopg.Connection, key: str, version: str, name: str, description: str, params: Params) -> int:
    wanted = json.loads(json.dumps(params.as_dict()))
    row = conn.execute("select id, params from public.strategies where key = %s and version = %s", (key, version)).fetchone()
    if row is not None:
        if row[1] != wanted:
            raise StrategyChangedError(f"{key} {version}: stored parameters differ; publish a new version")
        return int(row[0])
    created = conn.execute(
        "insert into public.strategies (key, version, name, description, params) values (%s, %s, %s, %s, %s) returning id",
        (key, version, name, description, json.dumps(wanted)),
    ).fetchone()
    assert created is not None
    return int(created[0])


def has_open_signal(conn: psycopg.Connection, pair_id: int, tf: str, direction: str) -> bool:
    row = conn.execute(
        "select 1 from public.signals s where s.pair_id = %s and s.timeframe = %s and s.direction = %s "
        "and not exists (select 1 from public.signal_outcomes o where o.signal_id = s.id) limit 1",
        (pair_id, tf, direction),
    ).fetchone()
    return row is not None


def record_evaluation(conn: psycopg.Connection, pair_id: int, tf: str, strategy_id: int, d: Decision) -> str | None:
    """Insert the evaluation; returns its id, or None if this (pair, tf, strategy, as_of) was already evaluated."""
    row = conn.execute(
        "insert into public.signal_evaluations (pair_id, timeframe, strategy_id, as_of, decision, reason_codes, features) "
        "values (%s, %s, %s, %s, %s, %s, %s) on conflict (pair_id, timeframe, strategy_id, as_of) do nothing returning id",
        (pair_id, tf, strategy_id, d.as_of, d.decision, list(d.reasons), json.dumps(d.features)),
    ).fetchone()
    return str(row[0]) if row else None


def publish_signal(
    conn: psycopg.Connection, evaluation_id: str, pair_id: int, tf: str, strategy_id: int, d: Decision, visibility: str, params: Params
) -> dict[str, Any]:
    """Append a signal to the hash chain inside the caller's transaction."""
    assert d.proposal is not None and d.explanation is not None
    conn.execute("select pg_advisory_xact_lock(hashtext('signals_chain'))")
    now_row = conn.execute("select now()").fetchone()
    last = conn.execute("select content_hash from public.signals order by seq desc limit 1").fetchone()
    assert now_row is not None
    p = d.proposal
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()), "evaluation_id": evaluation_id, "pair_id": pair_id, "timeframe": tf,
        "strategy_id": strategy_id, "model_version_id": None, "direction": d.decision, "as_of": d.as_of,
        "published_at": now_row[0], "entry_low": p.entry_low, "entry_high": p.entry_high, "stop_loss": p.stop_loss,
        "take_profits": list(p.take_profits), "risk_reward": p.risk_reward, "confidence": None,
        "confidence_band": "not_calibrated", "entry_expires_at": p.entry_expires_at, "horizon_bars": p.horizon_bars,
        "assumptions": {**params.costs.as_dict(), "entry_window_bars": str(params.entry_window_bars), "resolver_version": RESOLVER_VERSION},
        "explanation": d.explanation, "visibility": visibility, "prev_hash": last[0] if last else None,
    }  # fmt: skip
    payload["content_hash"] = content_hash(payload)
    cols = (*HASHED_FIELDS, "content_hash")
    values = [json.dumps(payload[c]) if c in ("assumptions", "explanation") else payload[c] for c in cols]
    conn.execute(
        f"insert into public.signals ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",  # noqa: S608 - fixed column list
        values,
    )
    return payload


def load_chain(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select {', '.join(HASHED_FIELDS)}, content_hash from public.signals order by seq")  # noqa: S608
        rows = cur.fetchall()
    for r in rows:
        r["id"], r["evaluation_id"] = str(r["id"]), str(r["evaluation_id"])
    return rows


def open_signals(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select s.id, s.pair_id, s.timeframe, s.direction, s.published_at, s.entry_low, s.entry_high, s.stop_loss, "
            "s.take_profits, s.horizon_bars, s.assumptions from public.signals s "
            "where not exists (select 1 from public.signal_outcomes o where o.signal_id = s.id) order by s.seq"
        )
        return cur.fetchall()


def record_resolution(conn: psycopg.Connection, signal_id: str, res: Resolution) -> None:
    for ev in res.events:
        conn.execute(
            "insert into public.signal_events (signal_id, event_type, occurred_at, price) values (%s, %s, %s, %s) "
            "on conflict (signal_id, event_type) do nothing",
            (signal_id, ev.type, ev.occurred_at, ev.price),
        )
    if res.outcome is not None:
        conn.execute(
            "insert into public.signal_outcomes (signal_id, outcome, entry_price, exit_price, net_return, r_multiple, closed_at, resolver_version) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (signal_id) do nothing",
            (signal_id, res.outcome, res.entry_price, res.exit_price, res.net_return, res.r_multiple, res.closed_at, RESOLVER_VERSION),
        )


def latest_evaluation_time(conn: psycopg.Connection, pair_id: int, tf: str, strategy_id: int) -> datetime | None:
    row = conn.execute(
        "select max(as_of) from public.signal_evaluations where pair_id = %s and timeframe = %s and strategy_id = %s", (pair_id, tf, strategy_id)
    ).fetchone()
    return row[0] if row else None
