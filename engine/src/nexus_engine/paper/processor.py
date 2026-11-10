"""Applies paper orders and protections against fresh public prices (engine side).

Each order is processed in its own transaction under a lock on the account
row, so concurrent placement/cancellation cannot double-spend. Prices older
than `max_price_age` are never used: orders wait, and market orders that
cannot fill before they expire (5 minutes) are closed as expired.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..logs import log
from ..signals.risk import CostModel
from .fills import Fill, FillRejectedError, Position, apply_buy, apply_sell, fill_price, protection_trigger

logger = logging.getLogger("nexus.paper")


@dataclass
class PaperProcessor:
    conn: psycopg.Connection
    costs: CostModel = field(default_factory=CostModel)
    max_price_age: timedelta = timedelta(seconds=60)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _prices(self) -> dict[int, tuple[Decimal, datetime, datetime]]:
        rows = self.conn.execute("select pair_id, price, fetched_at, source_time from public.latest_prices").fetchall()
        self.conn.commit()
        cutoff = self.now() - self.max_price_age
        return {r[0]: (r[1], r[3], r[2]) for r in rows if r[2] >= cutoff}

    def _position(self, account_id: str, pair_id: int) -> Position:
        row = self.conn.execute(
            "select quantity, cost_basis, realized_pnl from public.paper_positions where account_id = %s and pair_id = %s", (account_id, pair_id)
        ).fetchone()
        return Position(*row) if row else Position()

    def _save(self, acct: dict[str, Any], pair_id: int, cash: Decimal, pos: Position, fill: Fill, reason: str,
              source_time: datetime, order_id: str | None, protect: tuple[Decimal | None, Decimal | None] | None) -> None:  # fmt: skip
        c = self.conn
        c.execute("update public.paper_accounts set cash_balance = %s where id = %s", (cash, acct["id"]))
        c.execute(
            "insert into public.paper_positions (account_id, pair_id, user_id, quantity, cost_basis, realized_pnl, stop_loss, take_profit) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (account_id, pair_id) do update set quantity = excluded.quantity, "
            "cost_basis = excluded.cost_basis, realized_pnl = excluded.realized_pnl, stop_loss = excluded.stop_loss, "
            "take_profit = excluded.take_profit, updated_at = now()",
            (acct["id"], pair_id, acct["user_id"], pos.quantity, pos.cost_basis, pos.realized_pnl,
             *(protect if protect is not None else (None, None))),
        )  # fmt: skip
        c.execute(
            "insert into public.paper_fills (order_id, account_id, user_id, pair_id, side, reason, price, quantity, fee, realized_pnl, price_source_time) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (order_id, acct["id"], acct["user_id"], pair_id, fill.side, reason, fill.price, fill.quantity, fill.fee, fill.realized_pnl, source_time),
        )

    def expire(self) -> int:
        cur = self.conn.execute(
            "update public.paper_orders set status = 'expired', reject_reason = 'not filled before expiry', closed_at = now() "
            "where status = 'pending' and expires_at < %s",
            (self.now(),),
        )
        self.conn.commit()
        return cur.rowcount

    def fill_orders(self, prices: dict[int, tuple[Decimal, datetime, datetime]]) -> dict[str, int]:
        stats = {"filled": 0, "rejected": 0}
        pending = self.conn.execute("select id from public.paper_orders where status = 'pending' order by created_at").fetchall()
        self.conn.commit()
        for (order_id,) in pending:
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute("select o.* from public.paper_orders o where o.id = %s", (order_id,))
                o = cur.fetchone()
                if o is None or o["pair_id"] not in prices:
                    self.conn.rollback()
                    continue
                cur.execute("select * from public.paper_accounts where id = %s and status = 'active' for update", (o["account_id"],))
                acct = cur.fetchone()
                cur.execute("select status from public.paper_orders where id = %s for update", (order_id,))
                still = cur.fetchone()
            if acct is None or still is None or still["status"] != "pending":
                self.conn.rollback()
                continue
            latest, source_time, _ = prices[o["pair_id"]]
            px = fill_price(o["side"], o["order_type"], latest, o["limit_price"], self.costs)
            if px is None:
                self.conn.rollback()
                continue
            pos = self._position(acct["id"], o["pair_id"])
            try:
                if o["side"] == "buy":
                    cash, new_pos, fill = apply_buy(acct["cash_balance"], pos, px, o["quantity"], self.costs)
                    old = self.conn.execute(
                        "select stop_loss, take_profit from public.paper_positions where account_id = %s and pair_id = %s", (acct["id"], o["pair_id"])
                    ).fetchone()
                    protect = (o["stop_loss"], o["take_profit"]) if (o["stop_loss"] or o["take_profit"]) else (old if old else None)
                else:
                    cash, new_pos, fill = apply_sell(acct["cash_balance"], pos, px, o["quantity"], self.costs)
                    protect = None if new_pos.quantity == 0 else self.conn.execute(
                        "select stop_loss, take_profit from public.paper_positions where account_id = %s and pair_id = %s", (acct["id"], o["pair_id"])
                    ).fetchone()  # fmt: skip
            except FillRejectedError as e:
                self.conn.execute("update public.paper_orders set status = 'rejected', reject_reason = %s, closed_at = now() where id = %s", (str(e), order_id))
                self.conn.commit()
                stats["rejected"] += 1
                continue
            self._save(acct, o["pair_id"], cash, new_pos, fill, o["order_type"], source_time, str(order_id), protect)
            self.conn.execute("update public.paper_orders set status = 'filled', closed_at = now() where id = %s", (order_id,))
            self.conn.commit()
            stats["filled"] += 1
        return stats

    def run_protections(self, prices: dict[int, tuple[Decimal, datetime, datetime]]) -> int:
        rows = self.conn.execute(
            "select p.account_id, p.pair_id from public.paper_positions p join public.paper_accounts a on a.id = p.account_id "
            "where a.status = 'active' and p.quantity > 0 and (p.stop_loss is not null or p.take_profit is not null)"
        ).fetchall()
        self.conn.commit()
        closed = 0
        for account_id, pair_id in rows:
            if pair_id not in prices:
                continue
            latest, source_time, _ = prices[pair_id]
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute("select * from public.paper_accounts where id = %s and status = 'active' for update", (account_id,))
                acct = cur.fetchone()
                cur.execute("select * from public.paper_positions where account_id = %s and pair_id = %s for update", (account_id, pair_id))
                p = cur.fetchone()
            if acct is None or p is None or p["quantity"] <= 0:
                self.conn.rollback()
                continue
            reason = protection_trigger(latest, p["stop_loss"], p["take_profit"])
            if reason is None:
                self.conn.rollback()
                continue
            pos = Position(p["quantity"], p["cost_basis"], p["realized_pnl"])
            px = fill_price("sell", "market", latest, None, self.costs)
            assert px is not None
            cash, new_pos, fill = apply_sell(acct["cash_balance"], pos, px, pos.quantity, self.costs)
            self._save(acct, pair_id, cash, new_pos, fill, reason, source_time, None, None)
            self.conn.commit()
            closed += 1
            log(logger, logging.INFO, "paper_protection", reason=reason, account_id=str(account_id))
        return closed

    def snapshot(self, prices: dict[int, tuple[Decimal, datetime, datetime]]) -> int:
        hour = self.now().replace(minute=0, second=0, microsecond=0)
        accounts = self.conn.execute("select id, cash_balance from public.paper_accounts where status = 'active'").fetchall()
        n = 0
        for account_id, cash in accounts:
            positions = self.conn.execute(
                "select pair_id, quantity from public.paper_positions where account_id = %s and quantity > 0", (account_id,)
            ).fetchall()
            if any(pid not in prices for pid, _ in positions):
                continue  # never value a position with a stale or missing price
            equity = cash + sum((q * prices[pid][0] for pid, q in positions), Decimal(0))
            n += self.conn.execute(
                "insert into public.paper_equity_snapshots (account_id, taken_at, equity, cash) values (%s, %s, %s, %s) on conflict do nothing",
                (account_id, hour, equity.quantize(Decimal("1e-8")), cash),
            ).rowcount
        self.conn.commit()
        return n

    def process(self) -> dict[str, int]:
        expired = self.expire()
        prices = self._prices()
        stats = self.fill_orders(prices)
        stats.update({"expired": expired, "protections": self.run_protections(prices), "snapshots": self.snapshot(prices)})
        return stats
