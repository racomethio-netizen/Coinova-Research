"""Paper grid bot worker: runs ``nexus_strategies.grid`` on the LIVE Binance order book with
simulated money. Every fill, mark and stop is written to ``grid_paper_events``
(append-only).

* **Price feed.** The WebSocket order book that market data already keeps in Redis. No
  extra Binance requests are made. A stale or missing book means no fills: the grid
  waits and records a gap. Nothing is estimated.
* **Range.** The last 7 days of stored 1h candles (high/low, padded). Width is kept within
  4-12%. The number of levels is chosen so each step is at least 0.5% (above two 0.1%
  fees with margin), up to 20 levels.
* **Capital.** ``NEXUS_GRID_CAPITAL`` simulated USDT (default 1,000). The worst case is
  computed and must be at most 10% of it, or the grid is refused.
* **Kill switch** (engaged or unreadable): the grid pauses, with no fills.
* **Stop-loss**: the grid sells everything and stays stopped until the owner restarts it
  from the dashboard. There is no automatic recovery.

PAPER ONLY. This worker never places an exchange order.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_strategies import grid

log = get_logger("nexus.grid")

STATUS_KEY = "nexus:grid:status"
RESTART_KEY = "nexus:grid:restart"
HEARTBEAT = "grid:paper"
TICK_SECONDS = 10
BOOK_MAX_AGE = 30.0
RANGE_HOURS = 168
MIN_STEP = Decimal("0.005")
MAX_LEVELS = 20


def fit_levels(lower: Decimal, upper: Decimal, *, max_levels: int = MAX_LEVELS,
               min_step: Decimal = MIN_STEP) -> int:
    """Most levels (at most ``max_levels``) whose geometric step is >= ``min_step``."""
    steps = math.floor(math.log(float(upper / lower)) / math.log(1 + float(min_step)))
    return max(3, min(max_levels, steps + 1))


def config_to_json(c: grid.GridConfig) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in c.__dict__.items()}


def config_from_json(d: dict[str, Any]) -> grid.GridConfig:
    dec = {"lower", "upper", "capital", "stop_loss_pct", "maker_fee", "taker_fee",
           "stop_slippage", "amount_step", "price_tick", "min_notional", "max_loss_fraction",
           "min_step_over_fees"}
    kw: dict[str, Any] = {k: (Decimal(v) if k in dec else v) for k, v in d.items()}
    return grid.GridConfig(**kw)


def design(symbol: str, highs: list[Decimal], lows: list[Decimal], last: Decimal,
           capital: Decimal) -> tuple[grid.GridConfig | None, list[str]]:
    """The grid for the current market, or the reasons it is refused."""
    if len(highs) < 24:
        return None, [f"only {len(highs)} hourly candles (need 24+) to set the range"]
    lo, hi = grid.auto_range(highs, lows, last, max_width=Decimal("0.12"))
    cfg = grid.GridConfig(symbol=symbol, lower=lo.quantize(Decimal("0.01")),
                          upper=hi.quantize(Decimal("0.01")), levels=fit_levels(lo, hi),
                          capital=capital)
    problems = grid.validate(cfg)
    return (None, problems) if problems else (cfg, [])


class GridLedger:
    def __init__(self, factory: Any) -> None:
        self.factory = factory

    def latest(self) -> tuple[str, int, dict[str, Any]] | None:
        from nexus_db import models, transaction
        from sqlalchemy import select

        with transaction(self.factory) as s:
            row = s.scalar(select(models.GridPaperEvent)
                           .where(models.GridPaperEvent.kind != "gap")
                           .order_by(models.GridPaperEvent.id.desc()).limit(1))
            if row is None or "state" not in row.data:
                return None
            return row.run, row.seq, dict(row.data)

    def append(self, run: str, seq: int, symbol: str, kind: str, at: datetime,
               data: dict[str, Any]) -> None:
        from nexus_db import models, transaction

        with transaction(self.factory) as s:
            s.add(models.GridPaperEvent(run=run, seq=seq, symbol=symbol, kind=kind, at=at,
                                        data=json.loads(json.dumps(data, default=str))))


class GridRunner:
    def __init__(self, settings: Settings, factory: Any, redis: Any, *,
                 ledger: GridLedger | None = None) -> None:
        self.settings, self.factory, self.redis = settings, factory, redis
        self.ledger = ledger or GridLedger(factory)
        self.symbol = settings.grid_symbol
        self.capital = Decimal(str(settings.grid_capital))
        self.run: str | None = None
        self.seq = 0
        self.state: grid.GridState | None = None
        self.refused: list[str] = []
        self.last_mark: datetime | None = None
        self.last_gap: datetime | None = None
        self.last_book_at: str | None = None
        self.recent: list[dict[str, Any]] = []

    def bootstrap(self) -> None:
        got = self.ledger.latest()
        if got is None:
            return
        self.run, self.seq, data = got
        cfg = config_from_json(data["config"])
        self.state = grid.GridState.from_json(cfg, data["state"])
        log.info("grid_resumed", run=self.run, status=self.state.status)

    def _event(self, kind: str, at: datetime, extra: dict[str, Any]) -> None:
        assert self.run is not None and self.state is not None
        self.seq += 1
        self.ledger.append(self.run, self.seq, self.symbol, kind, at,
                           {**extra, "config": config_to_json(self.state.cfg),
                            "state": self.state.to_json()})

    def _candles(self) -> tuple[list[Decimal], list[Decimal]]:
        from nexus_db import repo, transaction

        with transaction(self.factory) as s:
            pid = repo.pair_id(s, "binance", self.symbol)
            rows = repo.load_candles(s, pid, "1h", limit=RANGE_HOURS)
        return [r.high for r in rows], [r.low for r in rows]

    async def _book(self) -> tuple[Decimal, Decimal] | None:
        from nexus_market.runner import read_ws_book

        book = await read_ws_book(self.redis, "binance", self.symbol, max_age=BOOK_MAX_AGE)
        if book is None or book.best_bid is None or book.best_ask is None:
            return None
        self.last_book_at = book.timestamp.isoformat()
        return book.best_bid, book.best_ask

    async def _killed(self) -> bool:
        from nexus_risk.kill_switch import KillSwitch

        try:
            return await KillSwitch(self.redis).status() is not None
        except Exception:
            return True                                     # unreadable: fail closed

    async def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        restart = await self.redis.getdel(RESTART_KEY)
        quote = await self._book()
        paused = await self._killed()
        if quote is not None and not paused and (
                self.state is None or (restart and self.state.status == "STOPPED")):
            highs, lows = await asyncio.to_thread(self._candles)
            mid = (quote[0] + quote[1]) / 2
            cfg, self.refused = design(self.symbol, highs, lows, mid, self.capital)
            if cfg is not None:
                was_restart = self.state is not None
                self.state = grid.start(cfg, mid)
                self.run, self.seq = f"grid-{now:%Y%m%d%H%M}", 0
                lv = grid.build_levels(cfg)
                self._event("restart" if was_restart else "start", now, {
                    "price": str(mid), "worst_case_loss": str(grid.worst_case_loss(cfg, lv)),
                    "step_pct": str(grid.step_pct(lv)), "by": "owner" if restart else "auto"})
                log.info("grid_started", run=self.run, lower=str(cfg.lower),
                         upper=str(cfg.upper), levels=cfg.levels)
        if self.state is not None and quote is not None and not paused:
            before = self.state.status
            for f in grid.step(self.state, quote[0], quote[1]):
                fill = {"side": f.side, "level": f.level, "price": str(f.price),
                        "qty": str(f.qty), "fee": str(f.fee), "reason": f.reason,
                        "profit": None if f.profit is None else str(f.profit)}
                self.recent = [{"at": now.isoformat(), **fill}, *self.recent][:50]
                self._event("fill", now, {"fill": fill})
            if self.state.status == "STOPPED" and before != "STOPPED":
                self._event("stop", now, {"reason": self.state.stop_reason})
            if self.last_mark is None or now - self.last_mark >= timedelta(hours=1):
                self.last_mark = now
                self._event("mark", now, {"mark": grid.mark(self.state, quote[0])})
        elif self.state is not None and quote is None and (
                self.last_gap is None or now - self.last_gap >= timedelta(hours=1)):
            self.last_gap = now
            self._event("gap", now, {"reason": f"no order book fresher than {BOOK_MAX_AGE:.0f}s"})
        status = self.status(now, quote, paused)
        await self.redis.set(STATUS_KEY, json.dumps(status, default=str), ex=600)
        await self.redis.set(f"nexus:heartbeat:{HEARTBEAT}", now.isoformat(), ex=600)
        return status

    def status(self, now: datetime, quote: tuple[Decimal, Decimal] | None,
               paused: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"paper": True, "symbol": self.symbol, "run": self.run,
                               "updated_at": now.isoformat(), "book_at": self.last_book_at,
                               "kill_switch_pause": paused, "data_ok": quote is not None,
                               "refused": self.refused, "recent_fills": self.recent}
        if quote is not None:
            out["bid"], out["ask"] = str(quote[0]), str(quote[1])
        if self.state is not None:
            st = self.state
            lv = st.levels
            out["grid"] = st.to_json()
            out["worst_case_loss"] = str(grid.worst_case_loss(st.cfg, lv))
            out["step_pct"] = str(grid.step_pct(lv))
            if quote is not None:
                out["mark"] = grid.mark(st, quote[0])
        return out


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    if settings.grid_bot_mode == "off":
        log.info("grid_not_started", reason="NEXUS_GRID_BOT_MODE=off")
        return
    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        runner = GridRunner(settings, factory, redis)
        await asyncio.to_thread(runner.bootstrap)
        log.info("grid_bot_started", symbol=runner.symbol, capital=str(runner.capital))
        while not stop.is_set():
            try:
                await runner.cycle()
            except Exception as exc:              # one bad tick must not stop the bot
                log.error("grid_cycle_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                pass
    finally:
        await redis.aclose()
        engine.dispose()


async def main() -> None:
    import signal

    from nexus_common.logging import configure_logging
    from nexus_config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


if __name__ == "__main__":
    asyncio.run(main())
