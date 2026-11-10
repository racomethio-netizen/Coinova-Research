"""Paper trading end to end: user places orders through the SQL functions; the engine
(as `nexus_engine`) fills them, runs stop/take-profit, and snapshots equity."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import psycopg
import pytest

from nexus_engine.paper.processor import PaperProcessor

pytestmark = pytest.mark.db


@pytest.fixture
def world(engine_conn: psycopg.Connection, superuser: psycopg.Connection):
    superuser.execute("truncate public.paper_fills, public.paper_orders, public.paper_positions, public.paper_equity_snapshots, public.paper_accounts cascade")
    uid = str(superuser.execute("insert into auth.users (email) values (gen_random_uuid()::text || '@test.local') returning id").fetchone()[0])
    btc = superuser.execute("select id from public.market_pairs where symbol = 'BTCUSDT'").fetchone()[0]

    def as_user(sql: str, *args: object):
        with superuser.transaction():
            superuser.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": uid, "role": "authenticated"}),))
            superuser.execute("set local role authenticated")
            return superuser.execute(sql, args).fetchone()

    def price(p: str, age_s: int = 0) -> None:
        superuser.execute(
            "insert into public.latest_prices (pair_id, price, source_time, fetched_at) values (%s, %s, now(), now() - make_interval(secs => %s)) "
            "on conflict (pair_id) do update set price = excluded.price, source_time = excluded.source_time, fetched_at = excluded.fetched_at",
            (btc, D(p), age_s),
        )

    as_user("select public.open_paper_account(10000)")
    return uid, btc, as_user, price, superuser


def account(conn: psycopg.Connection):
    row = conn.execute("select cash_balance from public.paper_accounts where status = 'active'").fetchone()
    conn.commit()
    return row[0]


def test_market_buy_then_stop_loss(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, _ = world
    price("100")
    as_user("select public.place_paper_order(%s, 'buy', 'market', 10, null, 95, 120)", btc)
    stats = PaperProcessor(engine_conn).process()
    assert stats["filled"] == 1
    assert account(engine_conn) == D("8998.49950000")
    pos = engine_conn.execute("select quantity, cost_basis, stop_loss, take_profit from public.paper_positions").fetchone()
    assert pos == (D("10.000000000000"), D("1001.50050000"), D("95.00000000"), D("120.00000000"))
    price("94")
    assert PaperProcessor(engine_conn).process()["protections"] == 1
    assert account(engine_conn) == D("9937.08997000")
    fills = engine_conn.execute("select side, reason, price, realized_pnl from public.paper_fills order by id").fetchall()
    assert fills == [("buy", "market", D("100.05000000"), None), ("sell", "stop_loss", D("93.95300000"), D("-62.91003000"))]
    assert engine_conn.execute("select quantity, realized_pnl from public.paper_positions").fetchone() == (D(0), D("-62.91003000"))


def test_limit_order_waits_until_marketable(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, _ = world
    price("100")
    as_user("select public.place_paper_order(%s, 'buy', 'limit', 1, 90)", btc)
    assert PaperProcessor(engine_conn).process()["filled"] == 0
    price("89")
    assert PaperProcessor(engine_conn).process()["filled"] == 1
    assert engine_conn.execute("select price from public.paper_fills").fetchone()[0] == D("89.04450000")


def test_stale_prices_never_fill_and_orders_expire(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, su = world
    price("100", age_s=600)
    as_user("select public.place_paper_order(%s, 'buy', 'market', 1)", btc)
    p = PaperProcessor(engine_conn)
    assert p.process()["filled"] == 0
    su.execute("update public.paper_orders set expires_at = now() - interval '1 second'")
    assert p.process()["expired"] == 1
    assert engine_conn.execute("select status from public.paper_orders").fetchone()[0] == "expired"
    # No open positions, so equity = cash and a snapshot is taken even though the price is stale (once per hour).
    assert engine_conn.execute("select equity, cash from public.paper_equity_snapshots").fetchall() == [(D("10000.00000000"), D("10000.00000000"))]
    assert p.snapshot(p._prices()) == 0


def test_insufficient_funds_is_rejected_and_cash_never_negative(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, _ = world
    price("100")
    as_user("select public.place_paper_order(%s, 'buy', 'market', 1000)", btc)
    assert PaperProcessor(engine_conn).process()["rejected"] == 1
    assert engine_conn.execute("select status, reject_reason from public.paper_orders").fetchone() == ("rejected", "insufficient_funds")
    assert account(engine_conn) == D("10000.00000000")


def test_cancelled_orders_are_not_filled(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, _ = world
    price("100")
    oid = as_user("select public.place_paper_order(%s, 'buy', 'limit', 1, 99)", btc)[0]
    as_user("select public.cancel_paper_order(%s)", oid)
    price("98")
    assert PaperProcessor(engine_conn).process()["filled"] == 0


def test_take_profit_and_snapshot(engine_conn: psycopg.Connection, world) -> None:
    _, btc, as_user, price, _ = world
    price("100")
    as_user("select public.place_paper_order(%s, 'buy', 'market', 2, null, 90, 110)", btc)
    p = PaperProcessor(engine_conn, now=lambda: datetime.now(UTC))
    p.process()
    snap = engine_conn.execute("select equity, cash from public.paper_equity_snapshots").fetchone()
    assert snap[0] == snap[1] + D(2) * D(100)  # valued at the latest (fresh) price
    price("111")
    assert p.process()["protections"] == 1
    assert engine_conn.execute("select reason from public.paper_fills order by id desc limit 1").fetchone()[0] == "take_profit"
    assert p.max_price_age == timedelta(seconds=60)
