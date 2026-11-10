"""Signal service: evaluate after each candle close, publish or record HOLD, resolve outcomes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from ..config import Settings
from ..logs import log
from ..marketdata.ingest import Pair
from ..marketdata.types import Candle
from ..timeframes import floor_to, tf_delta
from . import strategy as strat
from .execution import Bar, Plan, resolve
from .ledger import ensure_strategy, has_open_signal, open_signals, publish_signal, record_evaluation, record_resolution
from .risk import CostModel
from .series import HIGHER_TIMEFRAME, CandleSeries
from .strategy import Decision, Params

logger = logging.getLogger("nexus.signals")


def load_candles(conn: psycopg.Connection, pair_id: int, tf: str, until: datetime, bars: int) -> list[Candle]:
    """The last `bars` final candles that closed at or before `until` (oldest first)."""
    rows = conn.execute(
        "select open_time, close_time, open, high, low, close, volume, quote_volume, trade_count from public.market_candles "
        "where pair_id = %s and timeframe = %s and is_final and open_time + make_interval(secs => public.timeframe_seconds(timeframe)) <= %s "
        "order by open_time desc limit %s",
        (pair_id, tf, until, bars),
    ).fetchall()
    return [Candle(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], True) for r in reversed(rows)]


class SignalService:
    def __init__(
        self,
        conn: psycopg.Connection,
        settings: Settings,
        params: Params | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_publication_lag: timedelta = timedelta(minutes=10),
    ) -> None:
        self.conn = conn
        self.settings = settings
        self.params = params or Params()
        self.now = now
        # A signal is only published close to its candle close; a late (catch-up) evaluation is recorded as HOLD.
        self.max_publication_lag = max_publication_lag
        self.strategy_id = ensure_strategy(conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, self.params)
        conn.commit()

    def evaluate(self, pair: Pair, tf: str, as_of: datetime | None = None) -> Decision | None:
        """Evaluate at the most recent close boundary. Returns None if already evaluated."""
        as_of = as_of or floor_to(self.now(), tf)
        lookback = self.params.min_bars + 50
        own = CandleSeries.from_candles(tf, load_candles(self.conn, pair.id, tf, as_of, lookback))
        htf_tf = HIGHER_TIMEFRAME[tf]
        htf = CandleSeries.from_candles(htf_tf, load_candles(self.conn, pair.id, htf_tf, as_of, lookback)) if htf_tf else None
        d = strat.evaluate(own, htf, as_of, self.params)
        if d.decision != "HOLD":
            db_now = self.conn.execute("select now()").fetchone()
            assert db_now is not None
            if db_now[0] - as_of > self.max_publication_lag:
                d = replace(d, decision="HOLD", reasons=("publication_too_late", *d.reasons), proposal=None, explanation=None)
            elif has_open_signal(self.conn, pair.id, tf, d.decision):
                d = replace(d, decision="HOLD", reasons=("open_signal_exists", *d.reasons), proposal=None, explanation=None)
        try:
            eval_id = record_evaluation(self.conn, pair.id, tf, self.strategy_id, d)
            if eval_id is None:
                self.conn.rollback()
                return None
            if d.decision != "HOLD":
                published = publish_signal(self.conn, eval_id, pair.id, tf, self.strategy_id, d, self.settings.signals_visibility, self.params)
                log(
                    logger,
                    logging.INFO,
                    "signal_published",
                    pair=pair.symbol,
                    timeframe=tf,
                    direction=d.decision,
                    signal_id=published["id"],
                    visibility=self.settings.signals_visibility,
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return d

    def resolve_open(self) -> int:
        """Resolve open signals from candles that opened after publication. Returns outcomes recorded."""
        closed = 0
        for s in open_signals(self.conn):
            tf = s["timeframe"]
            step = tf_delta(tf)
            first_open = floor_to(s["published_at"], tf)
            if first_open < s["published_at"]:
                first_open += step
            entry_bars = int(s["assumptions"].get("entry_window_bars", self.params.entry_window_bars))
            needed = entry_bars + s["horizon_bars"] + 1
            rows = self.conn.execute(
                "select open_time, open, high, low, close from public.market_candles where pair_id = %s and timeframe = %s "
                "and is_final and open_time >= %s order by open_time limit %s",
                (s["pair_id"], tf, first_open, needed),
            ).fetchall()
            costs = CostModel(Decimal(s["assumptions"]["fee_rate"]), Decimal(s["assumptions"]["slippage_rate"]))
            plan = Plan(s["direction"], s["entry_low"], s["entry_high"], s["stop_loss"], s["take_profits"][0], entry_bars, s["horizon_bars"], costs)
            res = resolve(plan, [Bar(*r) for r in rows], tf, first_open)
            record_resolution(self.conn, str(s["id"]), res)
            self.conn.commit()
            if res.outcome is not None:
                closed += 1
                log(logger, logging.INFO, "signal_resolved", signal_id=str(s["id"]), outcome=res.outcome, r_multiple=str(res.r_multiple))
        return closed
