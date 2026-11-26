"""Daily research and paper forward test for stocks, forex and commodities
(multi-asset Stage 3). Each part obeys its market's switch (``nexus_common.markets``).

* **Stocks.**
  * RESEARCH: analysis of the NASDAQ-100 (FRED daily closes) and of Binance's stock/ETF
    perpetuals.
  * PAPER also runs the paper forward test of the volatility-managed NASDAQ-100 rule, the
    only stock strategy that passed its pre-registered test. It replays the UNCHANGED
    ``ndx_voltarget.ledger`` from START with 10,000 virtual USD and appends one row per
    new trading day to ``forward_test_events``.
* **Forex.** RESEARCH: the 7 majors against USD (FRED H.10) with their 3-month rate
  differential (carry).
* **Commodities.** RESEARCH: Binance commodity perpetuals (gold, silver, platinum,
  palladium, crude oil, natural gas, copper).

Rules:
* OFF means the market is skipped.
* Nothing is executed, and no broker is involved.
* Recorded days are never rewritten. A replay that disagrees with a recorded day is
  logged as a divergence.
* A failed download is reported as an error, never estimated.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from nexus_common.binance_guard import guarded_get
from nexus_common.instruments import BY_SYMBOL, of_market
from nexus_common.logging import get_logger
from nexus_common.markets import Autonomy, Market, at_least, effective
from nexus_common.sessions import is_open, next_open
from nexus_common.timeutil import utcnow

from nexus_backtest.multi_asset_trend import FX
from nexus_backtest.ndx_voltarget import TARGET, ledger
from nexus_backtest.trend_paper import daily_frame

log = get_logger("nexus.research.multiasset_paper")

RUN = "ndx-voltarget-2026-11-27"
START = pd.Timestamp("2026-11-27")
INITIAL = 10_000.0
STATUS_KEY = "nexus:multiasset:research"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FAPI = "https://fapi.binance.com/fapi/v1/klines"
HISTORY_DAYS = 500
DIVERGENCE_TOL = 0.001
REVIEW_DAYS = 180
STOCK_PERPS = of_market(Market.STOCKS, "binance_stock_perps")
COMMODITY_PERPS = of_market(Market.COMMODITIES, "binance_commodity_perps")


def with_session(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Add the instrument's trading session: open now, or when it reopens."""
    inst = BY_SYMBOL.get(str(row.get("symbol")))
    if inst is None:
        return row
    row["session"] = inst.session
    row["open_now"] = is_open(inst.session, now)
    if not row["open_now"]:
        nxt = next_open(inst.session, now)
        row["next_open"] = nxt.isoformat() if nxt else None
    return row


# ------------------------------------------------------------------------- analysis
def analyse(close: pd.Series, *, ann: int) -> dict[str, Any]:
    """Descriptive state of one instrument from its daily closes (no prediction)."""
    c = close.dropna()
    if len(c) < 2:
        return {"error": "not enough data"}
    def ret(n: int) -> float | None:
        return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
    sma = c.rolling(200).mean().iloc[-1] if len(c) >= 200 else None
    vol = c.pct_change().iloc[-30:].std() * math.sqrt(ann) if len(c) > 30 else None
    return {"as_of": str(c.index[-1].date()), "last": float(c.iloc[-1]),
            "return_1m": ret(21), "return_3m": ret(63), "return_12m": ret(252),
            "above_200d": None if sma is None else bool(c.iloc[-1] > sma),
            "vol_30d": None if vol is None or math.isnan(vol) else float(vol),
            "history_days": len(c)}


def replay(ndx: pd.Series) -> list[dict[str, Any]]:
    """One record per trading day since START (empty before the first one)."""
    if ndx.empty or ndx.index[-1] < START:
        return []
    led = ledger(ndx, start=START, initial=INITIAL)
    base = float(ndx[ndx.index >= START].iloc[0])
    return [{"date": str(pd.Timestamp(str(d)).date()), "equity": float(row["equity"]),
             "hold_equity": INITIAL * float(row["close"]) / base,
             "weight": float(row["weight"]), "target": row["target"], "vol": row["vol"],
             "close": float(row["close"]),
             "trade": {"notional": float(row["trade"]), "fee": float(row["fee"])}
             if row["trade"] else None} for d, row in led.iterrows()]


def exness_rows(factory: Any, market: Market) -> list[dict[str, Any]]:
    """Analysis of the daily bars the read-only Exness bridge has stored for this market
    (``market_candles``, source mt5). Empty when no bridge has reported."""
    from nexus_db import models, transaction
    from sqlalchemy import select

    out = []
    with transaction(factory) as s:
        pairs = s.execute(select(models.TradingPair.id, models.TradingPair.symbol,
                                 models.TradingPair.exchange_symbol)
                          .join(models.Exchange, models.Exchange.id == models.TradingPair.exchange_id)
                          .where(models.Exchange.code == "exness",
                                 models.TradingPair.asset_class == market.value)
                          .order_by(models.TradingPair.symbol)).all()
        for pid, sym, native in pairs:
            rows = s.execute(select(models.MarketCandle.open_time, models.MarketCandle.close)
                             .where(models.MarketCandle.pair_id == pid,
                                    models.MarketCandle.timeframe == "1d")
                             .order_by(models.MarketCandle.open_time.desc()).limit(400)).all()
            if not rows:
                continue
            ser = pd.Series([float(c) for _, c in rows][::-1],
                            index=pd.DatetimeIndex([t for t, _ in rows][::-1]))
            out.append({"symbol": f"EXNESS:{sym}", "name": f"{sym} (Exness {native})",
                        **analyse(ser, ann=252)})
    return out


# ---------------------------------------------------------------------------- fetch
async def fetch_fred(client: httpx.AsyncClient, series: str, since: datetime) -> pd.Series:
    r = await client.get(FRED_URL, params={"id": series, "cosd": since.strftime("%Y-%m-%d")})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.to_numeric(df[series], errors="coerce")
    s.index = pd.to_datetime(df["observation_date"])
    return s.dropna()


async def fetch_perp(client: httpx.AsyncClient, redis: Any, symbol: str,
                     now: datetime) -> pd.Series:
    r = await guarded_get(client, redis, FAPI, {"symbol": symbol, "interval": "1d",
                                                "limit": 400}, cache_ttl=3600)
    if r.status_code == 451 or "restricted location" in r.text[:300].lower():
        raise PermissionError("Binance refuses this server's location")
    r.raise_for_status()
    return daily_frame(r.json(), now)["close"]


# ----------------------------------------------------------------------------- step
async def _section(fetches: dict[str, Any], *, ann: int, names: dict[str, str],
                   now: datetime) -> dict[str, Any]:
    out, errors = [], []
    for key, coro in fetches.items():
        try:
            series = await coro
            out.append(with_session({"symbol": key, "name": names.get(key, key),
                                     **analyse(series, ann=ann)}, now))
        except Exception as exc:              # reported, never estimated
            errors.append(f"{key}: {type(exc).__name__}: {str(exc)[:120]}")
    return {"instruments": out, "errors": errors}


async def step(factory: Any, redis: Any, client: httpx.AsyncClient,
               now: datetime | None = None) -> dict[str, Any]:
    from nexus_db import repo, transaction

    now = now or utcnow()
    since = now - timedelta(days=HISTORY_DAYS)
    try:
        with transaction(factory) as s:
            rows = repo.market_autonomy_db_state(s)
        auto = {m: effective(m, rows) for m in (Market.STOCKS, Market.FOREX, Market.COMMODITIES)}
    except Exception as exc:                  # fail closed
        log.error("market_autonomy_unreadable", error=type(exc).__name__)
        auto = dict.fromkeys((Market.STOCKS, Market.FOREX, Market.COMMODITIES), Autonomy.OFF)
    status: dict[str, Any] = {"at": now.isoformat(), "paper": True, "markets": {}}

    # Stocks: NASDAQ-100 research + paper forward test; Binance stock perpetuals.
    st: dict[str, Any] = {"autonomy": auto[Market.STOCKS].value}
    if at_least(auto[Market.STOCKS], Autonomy.RESEARCH):
        ndx: pd.Series | None = None
        try:
            ndx = await fetch_fred(client, "NASDAQ100", since)
            ndx_row = with_session({"symbol": "NDX", "name": "NASDAQ-100 (FRED)",
                                    **analyse(ndx, ann=252)}, now)
            vol = ndx_row.get("vol_30d")
            ndx_row["voltarget_weight"] = min(1.0, TARGET / vol) if vol else None
            st_errors: list[str] = []
        except Exception as exc:
            ndx_row, st_errors = None, [f"NDX: {type(exc).__name__}: {str(exc)[:120]}"]
        perps = await _section({k: fetch_perp(client, redis, k, now) for k in STOCK_PERPS},
                               ann=365, names=STOCK_PERPS, now=now)
        st["instruments"] = ([ndx_row] if ndx_row else []) + perps["instruments"] + \
            exness_rows(factory, Market.STOCKS)
        st["errors"] = st_errors + perps["errors"]
        if at_least(auto[Market.STOCKS], Autonomy.PAPER) and ndx is not None:
            st["forward"] = _record(factory, replay(ndx))
    status["markets"]["stocks"] = st

    # Forex: the 7 majors with their carry.
    fx: dict[str, Any] = {"autonomy": auto[Market.FOREX].value}
    if at_least(auto[Market.FOREX], Autonomy.RESEARCH):
        out, errors = [], []
        try:
            usd = await fetch_fred(client, "DTB3", since)
        except Exception as exc:
            usd, errors = pd.Series(dtype=float), [f"DTB3: {type(exc).__name__}"]
        for cur, (sid, inv, rid) in FX.items():
            try:
                # Market convention: USDJPY, USDCAD, USDCHF are quoted per USD (as FRED
                # publishes them); EURUSD, GBPUSD, AUDUSD, NZDUSD in USD per unit.
                spot = await fetch_fred(client, sid, since)
                pair = f"USD{cur}" if inv else f"{cur}USD"
                row = with_session({"symbol": pair, "name": pair, **analyse(spot, ann=252)},
                                   now)
                rate = await fetch_fred(client, rid, since - timedelta(days=400))
                if len(rate) and len(usd):
                    diff = float(rate.iloc[-1] - usd.iloc[-1]) / 100
                    row["carry"] = -diff if inv else diff        # for a long position
                    row["rate_as_of"] = str(rate.index[-1].date())
                out.append(row)
            except Exception as exc:
                errors.append(f"{cur}: {type(exc).__name__}: {str(exc)[:120]}")
        fx.update(instruments=out + exness_rows(factory, Market.FOREX), errors=errors)
    status["markets"]["forex"] = fx

    # Commodities: Binance commodity perpetuals.
    co: dict[str, Any] = {"autonomy": auto[Market.COMMODITIES].value}
    if at_least(auto[Market.COMMODITIES], Autonomy.RESEARCH):
        co.update(await _section({k: fetch_perp(client, redis, k, now)
                                  for k in COMMODITY_PERPS}, ann=365, names=COMMODITY_PERPS,
                                 now=now))
        co["instruments"] = co["instruments"] + exness_rows(factory, Market.COMMODITIES)
    status["markets"]["commodities"] = co

    await redis.set(STATUS_KEY, json.dumps(status, default=str), ex=3 * 86400)
    log.info("multiasset_research_step",
             **{k: v["autonomy"] for k, v in status["markets"].items()})
    return status


def _record(factory: Any, days: list[dict[str, Any]]) -> dict[str, int]:
    from nexus_db import models, transaction
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

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
                    s.add(models.ForwardTestEvent(run=RUN, symbol="NDX", kind="day",
                                                  key=d["date"], at=at, data=d))
                    new += 1
                elif abs(float(old["equity"]) - d["equity"]) > \
                        DIVERGENCE_TOL * float(old["equity"]):
                    s.add(models.ForwardTestEvent(
                        run=RUN, symbol="NDX", kind="divergence", key=d["date"], at=at,
                        data={"recorded": old["equity"], "replay": d["equity"]}))
                    diverged += 1
        except IntegrityError:
            pass
    return {"new_days": new, "divergences": diverged}


def summary(days: list[dict[str, Any]], divergences: int) -> dict[str, Any]:
    out: dict[str, Any] = {"run": RUN, "strategy": "volatility-managed NASDAQ-100 (15%)",
                           "started_at": str(START.date()),
                           "review_at": str((START + timedelta(days=REVIEW_DAYS)).date()),
                           "initial": INITIAL, "days": len(days),
                           "divergences": divergences, "paper": True}
    if not days:
        out["message"] = "waiting for the first trading day after the start"
        return out
    last = days[-1]
    out.update(equity=last["equity"], hold_equity=last["hold_equity"], weight=last["weight"],
               return_since_start=last["equity"] / INITIAL - 1,
               hold_return=last["hold_equity"] / INITIAL - 1,
               trades=[{"date": d["date"], **d["trade"]} for d in days if d.get("trade")])
    return out


async def serve(settings: Any, stop: asyncio.Event) -> None:
    from nexus_common.proxy import httpx_kwargs
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=True, **httpx_kwargs()) as client:
            while not stop.is_set():
                try:
                    await step(factory, redis, client)
                except Exception as exc:
                    log.error("multiasset_research_failed",
                              error=f"{type(exc).__name__}: {exc}"[:300])
                try:
                    await asyncio.wait_for(stop.wait(), timeout=6 * 3600)
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
