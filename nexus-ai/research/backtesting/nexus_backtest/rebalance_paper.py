"""Forward PAPER test of the rebalancing bot that passed its pre-registered backtest
(docs/research/2026-11-26-rebalancing-xsmom-preregistration.md): basket R3 = BTC 40% /
ETH 40% / USDT 20%, rebalanced at the daily close when any weight drifts more than 5
points.

Daily after the 00:00 UTC close: fetch CLOSED daily candles, replay the UNCHANGED
``rebalance_xsmom.rebalance`` from START with 10,000 virtual USDT, and append one ledger
row per new day (``forward_test_events``). Recorded days are never rewritten. A replay
that disagrees with a recorded day is logged as a divergence. Nothing is executed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

from nexus_backtest.rebalance_xsmom import BASKETS, rebalance
from nexus_backtest.trend_paper import fetch

log = get_logger("nexus.research.rebalance_paper")

RUN = "rebalance-r3-2026-11-27"
START = datetime(2026, 11, 27, tzinfo=UTC)
INITIAL = 10_000.0
BASKET = BASKETS["R3_BTC40_ETH40_USDT20"]
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
STATUS_KEY = "nexus:forward:rebalance:status"
DIVERGENCE_TOL = 0.001
REVIEW_DAYS = 180


def replay(bars: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    start = pd.Timestamp(START)
    closes = pd.DataFrame({c: bars[s]["close"] for c, s in SYMBOLS.items()}).dropna()
    if closes.empty or closes.index[-1] < start:
        return []
    res = rebalance(closes, BASKET, start=start, end=pd.Timestamp("2100-01-01", tz="UTC"),
                    initial=INITIAL)
    trades = {t["date"]: t for t in res["log"]}
    return [{"date": str(d.date()), "equity": float(e), "hold_equity": float(h),
             "closes": {c: float(closes.loc[d, c]) for c in SYMBOLS},
             "rebalance": trades.get(str(d.date()))}
            for d, e, h in zip(res["equity"].index, res["equity"], res["hold"], strict=True)]


async def step(factory: Any, redis: Any, client: httpx.AsyncClient,
               now: datetime | None = None) -> dict[str, Any]:
    from nexus_db import models, transaction
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    now = now or utcnow()
    try:
        bars = {s: await fetch(client, s, now, redis) for s in SYMBOLS.values()}
    except Exception as exc:                 # recorded as a gap; nothing is estimated
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            with transaction(factory) as s:
                s.add(models.ForwardTestEvent(run=RUN, symbol="PORTFOLIO", kind="gap",
                                              key=str(day.date()), at=day,
                                              data={"reason": reason}))
        except IntegrityError:
            pass
        status = {"at": now.isoformat(), "ok": False, "error": reason}
        await redis.set(STATUS_KEY, json.dumps(status), ex=3 * 86400)
        return status
    days = replay(bars)
    with transaction(factory) as s:
        recorded = {r.key: r.data for r in s.scalars(select(models.ForwardTestEvent).where(
            models.ForwardTestEvent.run == RUN, models.ForwardTestEvent.kind == "day"))}
    new = diverged = 0
    for d in days:
        old = recorded.get(d["date"])
        at = datetime.fromisoformat(d["date"]).replace(tzinfo=UTC)
        try:
            with transaction(factory) as s:
                if old is None:
                    s.add(models.ForwardTestEvent(run=RUN, symbol="PORTFOLIO", kind="day",
                                                  key=d["date"], at=at, data=d))
                    new += 1
                elif abs(float(old["equity"]) - d["equity"]) > \
                        DIVERGENCE_TOL * float(old["equity"]):
                    s.add(models.ForwardTestEvent(
                        run=RUN, symbol="PORTFOLIO", kind="divergence", key=d["date"], at=at,
                        data={"recorded": old["equity"], "replay": d["equity"]}))
                    diverged += 1
        except IntegrityError:
            pass
    status = {"at": now.isoformat(), "ok": True, "new_days": new, "divergences": diverged,
              "latest": days[-1] if days else None}
    await redis.set(STATUS_KEY, json.dumps(status, default=str), ex=3 * 86400)
    log.info("rebalance_paper_step", new_days=new, divergences=diverged)
    return status


def summary(days: list[dict[str, Any]], divergences: int, gaps: list[str],
            now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {"run": RUN, "basket": BASKET, "started_at": START.isoformat(),
                           "review_at": (START + timedelta(days=REVIEW_DAYS)).isoformat(),
                           "initial": INITIAL, "days": len(days), "divergences": divergences,
                           "gap_days": gaps, "paper": True}
    if not days:
        out["message"] = "waiting for the first closed day after the start"
        return out
    last = days[-1]
    out.update(equity=last["equity"], hold_equity=last["hold_equity"],
               return_since_start=last["equity"] / INITIAL - 1,
               hold_return=last["hold_equity"] / INITIAL - 1,
               rebalances=[d["rebalance"] for d in days if d.get("rebalance")],
               equity_curve=[{"date": d["date"], "equity": d["equity"],
                              "hold": d["hold_equity"]} for d in days])
    return out


async def serve(settings: Any, stop: asyncio.Event) -> None:
    from nexus_common.proxy import httpx_kwargs
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=True, **httpx_kwargs()) as client:
            while not stop.is_set():
                try:
                    await step(factory, redis, client)
                except Exception as exc:
                    log.error("rebalance_paper_failed",
                              error=f"{type(exc).__name__}: {exc}"[:300])
                now = utcnow()
                nxt = (now + timedelta(days=1)).replace(hour=0, minute=9, second=0,
                                                         microsecond=0)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=(nxt - now).total_seconds())
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
