"""Forward PAPER test of the crypto trends ensemble (pre-registered:
docs/research/2026-11-23-crypto-trends-forward-preregistration.md).

Daily after the 00:00 UTC close: download the last 1,000 CLOSED daily candles, replay the
unchanged strategy (``crypto_trends.simulate``) from START with 10,000 virtual USDT, and
append one ledger row per new day. Recorded days are never rewritten; a replay that
disagrees with a recorded day is logged as a divergence. Nothing is executed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from nexus_common.binance_guard import BinanceBackoff, guarded_get
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow

from nexus_backtest.crypto_trends import SimConfig, max_drawdown, simulate

log = get_logger("nexus.research.trend_paper")

RUN = "crypto-trends-2026-11-24"
START = datetime(2026, 11, 24, tzinfo=UTC)
REVIEW_DAYS = 180
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INITIAL = 10_000.0
SPOT_API = "https://data-api.binance.vision/api/v3"
STATUS_KEY = "nexus:forward:trends:status"
DIVERGENCE_TOL = 0.001          # 0.1% of equity
GATES = {"min_return_180d": -0.066, "max_drawdown": 0.15, "max_gap_days": 3}


def daily_frame(rows: list[list[Any]], now: datetime) -> pd.DataFrame:
    """Binance kline rows -> CLOSED daily bars (open-time index, UTC midnight)."""
    now_ms = now.timestamp() * 1000
    rows = [r for r in rows if int(r[6]) < now_ms]
    idx = pd.DatetimeIndex([pd.Timestamp(int(r[0]), unit="ms", tz="UTC") for r in rows])
    return pd.DataFrame({"open": [float(r[1]) for r in rows],
                         "close": [float(r[4]) for r in rows]}, index=idx)


async def fetch(client: httpx.AsyncClient, symbol: str, now: datetime,
                redis: Any = None) -> pd.DataFrame:
    r = await guarded_get(client, redis, f"{SPOT_API}/klines",
                          {"symbol": symbol, "interval": "1d", "limit": 1000}, cache_ttl=600)
    if r.status_code == 451 or "restricted location" in r.text[:300].lower():
        raise PermissionError("Binance refuses this server's location")
    r.raise_for_status()
    df = daily_frame(r.json(), now)
    full = pd.date_range(df.index[0], df.index[-1], freq="1D", tz="UTC")
    if len(full) != len(df):
        raise ValueError(f"{symbol}: {len(full) - len(df)} missing daily candles")
    return df


def replay(bars: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """One record per closed day since START (empty before the first one)."""
    start = pd.Timestamp(START)
    if any(b.index[-1] < start for b in bars.values()):
        return []
    res = simulate(bars, SimConfig(), start=start, initial=INITIAL)
    trades = res.extra["trade_log"]
    out = []
    for d, eq in res.equity.items():
        day = str(pd.Timestamp(d).date())
        out.append({
            "date": day, "equity": float(eq),
            "weights": {s: float(res.weights.loc[d, s]) for s in res.weights},
            "targets": {s: float(res.extra["targets"].loc[d, s]) for s in res.weights},
            "closes": {s: float(bars[s]["close"].loc[d]) for s in bars},
            "trades": [t for t in trades if t["date"] == day]})
    return out


async def step(factory: Any, redis: Any, client: httpx.AsyncClient,
               now: datetime | None = None) -> dict[str, Any]:
    from nexus_db import models, transaction
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    now = now or utcnow()
    try:
        bars = {s: await fetch(client, s, now, redis) for s in SYMBOLS}
    except (httpx.HTTPError, PermissionError, ValueError, KeyError, IndexError,
            BinanceBackoff) as exc:
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
        log.warning("trend_paper_gap", error=reason)
        return status
    days = replay(bars)
    with transaction(factory) as s:
        recorded = {r.key: r.data for r in s.scalars(select(models.ForwardTestEvent).where(
            models.ForwardTestEvent.run == RUN, models.ForwardTestEvent.kind == "day"))}
    new = diverged = 0
    for d in days:
        old = recorded.get(d["date"])
        if old is not None:
            if abs(float(old["equity"]) - d["equity"]) > DIVERGENCE_TOL * float(old["equity"]):
                try:
                    with transaction(factory) as s:
                        s.add(models.ForwardTestEvent(
                            run=RUN, symbol="PORTFOLIO", kind="divergence", key=d["date"],
                            at=datetime.fromisoformat(d["date"]).replace(tzinfo=UTC),
                            data={"recorded": old["equity"], "replay": d["equity"]}))
                    diverged += 1
                except IntegrityError:
                    pass
            continue
        try:
            with transaction(factory) as s:
                s.add(models.ForwardTestEvent(
                    run=RUN, symbol="PORTFOLIO", kind="day", key=d["date"],
                    at=datetime.fromisoformat(d["date"]).replace(tzinfo=UTC), data=d))
            new += 1
        except IntegrityError:
            pass
    status = {"at": now.isoformat(), "ok": True, "new_days": new, "divergences": diverged,
              "last_closed_day": str(bars[SYMBOLS[0]].index[-1].date()),
              "latest": days[-1] if days else None}
    await redis.set(STATUS_KEY, json.dumps(status, default=str), ex=3 * 86400)
    log.info("trend_paper_step", new_days=new, divergences=diverged)
    return status


def summary(days: list[dict[str, Any]], divergences: int, gap_days: list[str],
            now: datetime) -> dict[str, Any]:
    """Dashboard view and the pre-registered review."""
    review = START + timedelta(days=REVIEW_DAYS)
    out: dict[str, Any] = {"run": RUN, "started_at": START.isoformat(),
                           "review_at": review.isoformat(), "days": len(days),
                           "divergences": divergences, "gap_days": gap_days,
                           "gates": GATES, "initial": INITIAL}
    if not days:
        out.update(message="waiting for the first closed day after the start")
        return out
    eq = pd.Series([d["equity"] for d in days], index=pd.to_datetime([d["date"] for d in days]))
    longest_gap, run_ = 0, 0
    gaps = set(gap_days)
    for d in pd.date_range(START.date(), now.date(), freq="1D"):
        run_ = run_ + 1 if str(d.date()) in gaps else 0
        longest_gap = max(longest_gap, run_)
    ret = float(eq.iloc[-1] / INITIAL - 1)
    out.update(equity=float(eq.iloc[-1]), return_since_start=ret,
               max_drawdown=max_drawdown(pd.concat([pd.Series([INITIAL]), eq])),
               latest=days[-1], longest_gap_days=longest_gap,
               equity_curve=[{"date": d["date"], "equity": d["equity"]} for d in days],
               trades=[t for d in days for t in d["trades"]][-50:])
    out["checks"] = {
        "integrity": divergences == 0,
        "return_ok": ret >= GATES["min_return_180d"],
        "drawdown_ok": out["max_drawdown"] <= GATES["max_drawdown"],
        "gaps_ok": longest_gap <= GATES["max_gap_days"]}
    out["decision"] = ("due: " + ("PASS" if all(out["checks"].values()) else "FAIL")
                       if now >= review else "pending")
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
                    log.error("trend_paper_failed", error=f"{type(exc).__name__}: {exc}"[:300])
                now = utcnow()
                nxt = (now + timedelta(days=1)).replace(hour=0, minute=7, second=0,
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
