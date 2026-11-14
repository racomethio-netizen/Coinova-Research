"""Schema acceptance tests on a real PostgreSQL, through the Alembic migrations."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from nexus_db import Base
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.db


def q(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params)


def violates(conn: Connection, sql: str, sqlstate: str, **params: Any) -> None:
    """Assert the statement fails with the given SQLSTATE, without aborting the test tx."""
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError) as info:
        q(conn, sql, **params)
    sp.rollback()
    assert info.value.orig is not None
    assert info.value.orig.sqlstate == sqlstate, info.value.orig  # type: ignore[attr-defined]


@pytest.fixture
def world(conn: Connection) -> dict[str, int]:
    user = q(conn, "INSERT INTO users (email, password_hash, role) "
                   "VALUES ('owner@example.test', 'x', 'owner') RETURNING id").scalar_one()
    pair = q(conn, "INSERT INTO trading_pairs (exchange_id, symbol, exchange_symbol, base_asset, "
                   "quote_asset, price_tick, amount_step) VALUES (1, 'BTC/USDT', 'BTCUSDT', "
                   "'BTC', 'USDT', 0.01, 0.00001) RETURNING id").scalar_one()
    pair_k = q(conn, "INSERT INTO trading_pairs (exchange_id, symbol, exchange_symbol, "
                     "base_asset, quote_asset) VALUES (2, 'BTC/USDT', 'XBTUSDT', 'BTC', 'USDT') "
                     "RETURNING id").scalar_one()
    paper = q(conn, "INSERT INTO portfolios (user_id, name, mode, initial_capital) "
                    "VALUES (:u, 'main', 'paper', 10000) RETURNING id", u=user).scalar_one()
    live = q(conn, "INSERT INTO portfolios (user_id, name, mode) VALUES (:u, 'main', 'live') "
                   "RETURNING id", u=user).scalar_one()
    return {"user": user, "pair": pair, "pair_kraken": pair_k, "paper": paper, "live": live}


def insert_order(conn: Connection, portfolio: int, mode: str, pair: int, **kw: Any) -> int:
    params = {"p": portfolio, "m": mode, "pair": pair, "cid": str(uuid.uuid4()),
              "amount": "0.001", "price": "60000", "filled": "0", **kw}
    return q(conn, "INSERT INTO orders (portfolio_id, mode, exchange_id, pair_id, "
                   "client_order_id, side, order_type, price, amount, filled_amount) "
                   "VALUES (:p, :m, 1, :pair, :cid, 'buy', 'limit', :price, :amount, :filled) "
                   "RETURNING id", **params).scalar_one()


# ------------------------------------------------------------------ migrations
def test_models_match_migrated_schema(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        ctx = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"models and migrations drifted: {diff}"


def test_all_required_tables_exist(migrated_engine: Engine) -> None:
    required = {
        "users", "exchanges", "exchange_credentials", "trading_pairs", "market_candles",
        "orderbook_snapshots", "indicators", "ai_models", "ai_predictions", "strategies",
        "trading_signals", "risk_events", "portfolios", "balances", "orders", "trades",
        "positions", "performance_snapshots", "backtest_runs", "model_evaluations",
        "system_events", "audit_logs",
    }
    with migrated_engine.connect() as c:
        tables = set(c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
    assert required <= tables


def test_money_columns_are_numeric_and_times_are_timestamptz(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as c:
        rows = c.execute(text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND (column_name IN ('price','amount','fee','free',"
            "'locked','equity','quantity','realized_pnl','fee_amount') "
            "OR column_name LIKE '%\\_at' OR column_name LIKE '%time')")).all()
    assert rows
    for table, column, dtype in rows:
        if column.endswith("_at") or column.endswith("time"):
            assert dtype == "timestamp with time zone", (table, column, dtype)
        else:
            assert dtype == "numeric", (table, column, dtype)
    assert not [r for r in rows if r[2] in ("real", "double precision")]


def test_downgrade_and_reupgrade(db_url: str, migrated_engine: Engine) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    with migrated_engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                              "AND tablename <> 'alembic_version'")).scalar_one()
    assert left == 0
    command.upgrade(cfg, "head")


def test_reference_data_seeded_disabled(conn: Connection) -> None:
    rows = q(conn, "SELECT code, is_enabled FROM exchanges ORDER BY id").all()
    assert rows == [("binance", False), ("kraken", False), ("coinbase", False)]
    strategies = q(conn, "SELECT code FROM strategies WHERE is_enabled").all()
    assert strategies == []
    # Nothing fabricated: no market data, balances, orders or performance rows.
    for table in ("market_candles", "balances", "orders", "trades", "performance_snapshots"):
        assert q(conn, f"SELECT count(*) FROM {table}").scalar_one() == 0  # noqa: S608


# ------------------------------------------------------------------ paper/live isolation
def test_live_order_cannot_reference_paper_portfolio(conn: Connection, world: dict[str, int]) -> None:
    insert_order(conn, world["paper"], "paper", world["pair"])
    violates(conn, "INSERT INTO orders (portfolio_id, mode, exchange_id, pair_id, client_order_id,"
                   " side, order_type, price, amount) VALUES (:p, 'live', 1, :pair, :cid, 'buy',"
                   " 'limit', 1, 1)", "23503",
             p=world["paper"], pair=world["pair"], cid=str(uuid.uuid4()))


def test_fill_mode_must_match_order(conn: Connection, world: dict[str, int]) -> None:
    oid = insert_order(conn, world["paper"], "paper", world["pair"])
    violates(conn, "INSERT INTO trades (order_id, mode, exchange_trade_id, price, amount, "
                   "executed_at) VALUES (:o, 'live', 't1', 1, 1, now())", "23503", o=oid)
    q(conn, "INSERT INTO trades (order_id, mode, exchange_trade_id, price, amount, executed_at) "
            "VALUES (:o, 'paper', 't1', 1, 0.001, now())", o=oid)


def test_mode_is_immutable(conn: Connection, world: dict[str, int]) -> None:
    violates(conn, "UPDATE portfolios SET mode='live' WHERE id=:p", "23514", p=world["paper"])


def test_balance_and_position_isolation(conn: Connection, world: dict[str, int]) -> None:
    violates(conn, "INSERT INTO balances (portfolio_id, mode, exchange_id, asset, free, locked, "
                   "source, synced_at) VALUES (:p, 'live', 1, 'USDT', 1, 0, 'internal', now())",
             "23503", p=world["paper"])
    violates(conn, "INSERT INTO positions (portfolio_id, mode, exchange_id, pair_id, base_asset,"
                   " quantity, avg_entry_price, cost_basis, opened_at) VALUES (:p, 'paper', 1, "
                   ":pair, 'BTC', 1, 1, 1, now())", "23503", p=world["live"], pair=world["pair"])


# ------------------------------------------------------------------ order integrity
def test_client_order_id_unique(conn: Connection, world: dict[str, int]) -> None:
    cid = str(uuid.uuid4())
    insert_order(conn, world["paper"], "paper", world["pair"], cid=cid)
    violates(conn, "INSERT INTO orders (portfolio_id, mode, exchange_id, pair_id, client_order_id,"
                   " side, order_type, price, amount) VALUES (:p, 'paper', 1, :pair, :cid, 'buy',"
                   " 'limit', 1, 1)", "23505", p=world["paper"], pair=world["pair"], cid=cid)


@pytest.mark.parametrize(("column_sql", "state"), [
    ("filled_amount = 2", "23514"),     # overfill
    ("amount = 0", "23514"),
    ("price = NULL", "23514"),          # limit without price
    ("status = 'weird'", "23514"),
    ("side = 'hold'", "23514"),       # only buy/sell exist
    ("fee_amount = -1", "23514"),
])
def test_order_checks(conn: Connection, world: dict[str, int], column_sql: str,
                      state: str) -> None:
    oid = insert_order(conn, world["paper"], "paper", world["pair"])
    violates(conn, f"UPDATE orders SET {column_sql} WHERE id=:o", state, o=oid)  # noqa: S608


def test_no_duplicate_open_exposure_across_exchanges(conn: Connection,
                                                     world: dict[str, int]) -> None:
    sql = ("INSERT INTO positions (portfolio_id, mode, exchange_id, pair_id, base_asset, quantity,"
           " avg_entry_price, cost_basis, opened_at, status, closed_at) VALUES (:p, 'paper', :ex,"
           " :pair, 'BTC', 0.01, 60000, 600, now(), :st, :closed)")
    q(conn, sql, p=world["paper"], ex=1, pair=world["pair"], st="open", closed=None)
    violates(conn, sql, "23505", p=world["paper"], ex=2, pair=world["pair_kraken"], st="open",
             closed=None)
    # Closed positions don't block; live portfolio is independent.
    q(conn, sql, p=world["paper"], ex=2, pair=world["pair_kraken"], st="closed", closed="2026-01-01Z")
    q(conn, sql.replace("'paper'", "'live'"), p=world["live"], ex=1, pair=world["pair"],
      st="open", closed=None)


def test_negative_balance_rejected(conn: Connection, world: dict[str, int]) -> None:
    violates(conn, "INSERT INTO balances (portfolio_id, mode, exchange_id, asset, free, locked, "
                   "source, synced_at) VALUES (:p, 'paper', 1, 'USDT', -1, 0, 'internal', now())",
             "23514", p=world["paper"])


def test_candle_consistency(conn: Connection, world: dict[str, int]) -> None:
    sql = ("INSERT INTO market_candles (pair_id, timeframe, open_time, open, high, low, close, "
           "volume) VALUES (:pair, :tf, '2026-01-01Z', :o, :h, :l, :c, 1)")
    q(conn, sql, pair=world["pair"], tf="5m", o=100, h=101, l=99, c=100.5)
    violates(conn, sql, "23505", pair=world["pair"], tf="5m", o=100, h=101, l=99, c=100.5)
    violates(conn, sql.replace("'2026-01-01Z'", "'2026-01-02Z'"), "23514",
             pair=world["pair"], tf="5m", o=100, h=99, l=98, c=100)
    violates(conn, sql.replace("'2026-01-01Z'", "'2026-01-03Z'"), "23514",
             pair=world["pair"], tf="1d", o=1, h=1, l=1, c=1)


def test_buy_signal_stop_and_target_sides(conn: Connection, world: dict[str, int]) -> None:
    violates(conn, "INSERT INTO trading_signals (mode, strategy_id, pair_id, side, timeframe, "
                   "bar_time, entry_price, stop_loss, take_profit, confidence) VALUES ('paper', 1,"
                   " :pair, 'buy', '1h', now(), 100, 101, 110, 0.6)", "23514", pair=world["pair"])


def test_one_active_model_per_name(conn: Connection) -> None:
    sql = ("INSERT INTO ai_models (name, version, algorithm, status, feature_set, hyperparameters)"
           " VALUES ('xgb_dir', :v, 'xgboost', 'active', '{}', '{}')")
    q(conn, sql, v="1")
    violates(conn, sql, "23505", v="2")


# ------------------------------------------------------------------ audit log
def test_audit_log_is_append_only(conn: Connection, world: dict[str, int]) -> None:
    aid = q(conn, "INSERT INTO audit_logs (user_id, actor, action) VALUES (:u, 'user:1', "
                  "'login') RETURNING id", u=world["user"]).scalar_one()
    violates(conn, "UPDATE audit_logs SET action='x' WHERE id=:a", "42501", a=aid)
    violates(conn, "DELETE FROM audit_logs WHERE id=:a", "42501", a=aid)
    violates(conn, "TRUNCATE audit_logs", "42501")


def test_credentials_one_active_per_exchange(conn: Connection, world: dict[str, int]) -> None:
    sql = ("INSERT INTO exchange_credentials (user_id, exchange_id, label, api_key_encrypted, "
           "api_secret_encrypted, key_fingerprint, encryption_key_id, revoked_at) "
           "VALUES (:u, 1, :label, '\\x00', '\\x00', 'fp', 'k1', :rev)")
    q(conn, sql, u=world["user"], label="a", rev=None)
    violates(conn, sql, "23505", u=world["user"], label="b", rev=None)
    q(conn, sql, u=world["user"], label="c", rev="2026-01-01Z")
