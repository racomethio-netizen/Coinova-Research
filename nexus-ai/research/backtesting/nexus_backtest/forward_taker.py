"""Forward PAPER test: taker flow in range regimes (pre-registered:
docs/research/2026-11-22-taker-range-forward-preregistration.md).

Every hour: download the latest CLOSED Binance candles, run the unchanged research code
(TakerFlow + study_signals) with the frozen parameters, and record each COMPLETED trade
whose signal closed after the start and whose regime was ``range``. Nothing is executed.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from nexus_common.binance_guard import BinanceBackoff, guarded_get
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_strategies.library_v2 import TakerFlow

from nexus_backtest.signals import CostModel, study_signals

log = get_logger("nexus.research.forward_taker")

RUN = "taker-range-2026-11-22"
START = datetime(2026, 11, 22, 12, 5, tzinfo=UTC)          # pre-registration commit time
SYMBOLS = ("BTCUSDT", "ETHUSDT")
PARAMS = {"ratio_min": 0.55, "stop_atr": 2.5, "target_atr": 3.0, "use_exit": 0.0}
COSTS = CostModel(fee_rate=0.001, slippage_rate=0.0005)
MAX_HOLD_H = 48
SPOT_API = "https://data-api.binance.vision/api/v3"
FAPI = "https://fapi.binance.com/fapi/v1"
STATUS_KEY = "nexus:forward:taker:status"
TARGET_TRADES = 30


def klines_frame(rows: list[list[Any]], now: datetime) -> pd.DataFrame:
    """Binance kline rows -> float OHLCV(+taker) frame of CLOSED bars, open-time index."""
    now_ms = now.timestamp() * 1000
    rows = [r for r in rows if int(r[6]) < now_ms]
    idx = pd.DatetimeIndex([pd.Timestamp(int(r[0]), unit="ms", tz="UTC") for r in rows],
                           name="open_time")
    return pd.DataFrame({"open": [float(r[1]) for r in rows], "high": [float(r[2]) for r in rows],
                         "low": [float(r[3]) for r in rows], "close": [float(r[4]) for r in rows],
                         "volume": [float(r[5]) for r in rows],
                         "taker": [float(r[9]) for r in rows]}, index=idx)


async def _klines(client: httpx.AsyncClient, base: str, symbol: str, interval: str,
                  limit: int, redis: Any = None) -> list[list[Any]]:
    r = await guarded_get(client, redis, f"{base}/klines",
                          {"symbol": symbol, "interval": interval, "limit": limit})
    if r.status_code == 451 or "restricted location" in r.text[:300].lower():
        raise PermissionError("Binance refuses this server's location")
    r.raise_for_status()
    rows: list[list[Any]] = r.json()
    return rows


async def load(client: httpx.AsyncClient, symbol: str, now: datetime,
               redis: Any = None) -> dict[str, pd.DataFrame]:
    m5 = klines_frame(await _klines(client, SPOT_API, symbol, "5m", 1000, redis), now)
    h1 = klines_frame(await _klines(client, SPOT_API, symbol, "1h", 1000, redis), now)
    h4 = klines_frame(await _klines(client, SPOT_API, symbol, "4h", 500, redis), now)
    perp = klines_frame(await _klines(client, FAPI, symbol, "1h", 1000, redis), now)
    h1["taker_buy_ratio"] = (perp["taker"] / perp["volume"].where(perp["volume"] > 0)
                             ).reindex(h1.index)
    return {"5m": m5.drop(columns="taker"), "1h": h1.drop(columns="taker"),
            "4h": h4.drop(columns="taker")}


def evaluate(frames: dict[str, pd.DataFrame]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(completed range trades since START, open range trades). Unchanged research code."""
    strat = TakerFlow(**PARAMS)
    trades = study_signals(frames, strat, costs=COSTS, max_hold_hours=MAX_HOLD_H,
                           execution_tf="5m")
    if trades.empty:
        return [], []
    last = frames["5m"].index[-1]
    done: list[dict[str, Any]] = []
    open_: list[dict[str, Any]] = []
    for sig_t, t in trades.iterrows():
        if sig_t < START or t["regime"] != "range":
            continue
        row = {"signal_time": sig_t.isoformat(), "entry_time": t["entry_time"].isoformat(),
               "exit_time": t["exit_time"].isoformat(), "entry": float(t["entry"]),
               "exit": float(t["exit"]), "stop": float(t["stop"]), "target": float(t["target"]),
               "ret": float(t["ret"]), "outcome": t["outcome"], "regime": t["regime"]}
        full_hold = t["exit_time"] >= t["entry_time"] + timedelta(hours=MAX_HOLD_H, minutes=-5)
        finished = t["outcome"] != "timeout" or full_hold
        (done if finished and t["exit_time"] <= last else open_).append(row)
    return done, open_


async def step(factory: Any, redis: Any, client: httpx.AsyncClient,
               now: datetime | None = None) -> dict[str, Any]:
    from nexus_db import models, transaction
    from sqlalchemy.exc import IntegrityError

    now = now or utcnow()
    status: dict[str, Any] = {"at": now.isoformat(), "symbols": {}}
    for sym in SYMBOLS:
        try:
            frames = await load(client, sym, now, redis)
            done, open_ = evaluate(frames)
            new = 0
            for tr in done:
                try:
                    with transaction(factory) as s:
                        s.add(models.ForwardTestEvent(run=RUN, symbol=sym, kind="trade",
                                                      key=tr["signal_time"][:25],
                                                      at=datetime.fromisoformat(tr["exit_time"]),
                                                      data=tr))
                    new += 1
                except IntegrityError:
                    pass                               # already recorded
            regime = None
            if not frames["1h"].empty:
                from nexus_strategies.regime import classify
                regime = classify(frames["1h"]).iloc[-1]
            status["symbols"][sym] = {"ok": True, "new_trades": new, "open": open_,
                                      "regime_now": None if regime is None else str(regime),
                                      "last_bar": frames["5m"].index[-1].isoformat()}
        except (httpx.HTTPError, PermissionError, KeyError, ValueError, IndexError,
                BinanceBackoff) as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            hour = now.replace(minute=0, second=0, microsecond=0)
            try:
                with transaction(factory) as s:
                    s.add(models.ForwardTestEvent(run=RUN, symbol=sym, kind="gap",
                                                  key=hour.isoformat()[:25], at=hour,
                                                  data={"reason": reason}))
            except IntegrityError:
                pass
            status["symbols"][sym] = {"ok": False, "error": reason}
            log.warning("forward_taker_gap", symbol=sym, error=reason)
    await redis.set(STATUS_KEY, json.dumps(status), ex=3 * 3600)
    log.info("forward_taker_step", status={k: v.get("ok") for k, v in status["symbols"].items()})
    return status


def summary(trades: list[dict[str, Any]], gaps: int, now: datetime) -> dict[str, Any]:
    rets = [float(t["ret"]) for t in trades]
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r <= 0]
    mean = sum(rets) / n if n else None
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 and mean is not None else None
    deadline = START + timedelta(days=548)
    return {
        "run": RUN, "started_at": START.isoformat(), "trades": n, "target_trades": TARGET_TRADES,
        "mean_return": mean, "win_rate": len(wins) / n if n else None,
        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None,
        "t_stat": (mean / (sd / math.sqrt(n))) if sd and mean is not None else None,
        "total_return_sum": sum(rets) if n else 0.0, "gap_hours": gaps,
        "deadline": deadline.isoformat(),
        "decision": "due" if n >= TARGET_TRADES or now >= deadline else "pending",
        "gates": {"mean_return_min": 0.0, "profit_factor_min": 1.15, "t_stat_min": 1.0},
        "params": PARAMS, "costs": {"fee_rate": COSTS.fee_rate,
                                    "slippage_rate": COSTS.slippage_rate},
    }


async def serve(settings: Any, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=True) as client:
            while not stop.is_set():
                try:
                    await step(factory, redis, client)
                except Exception as exc:
                    log.error("forward_taker_failed", error=f"{type(exc).__name__}: {exc}"[:300])
                now = utcnow()
                nxt = (now + timedelta(hours=1)).replace(minute=3, second=0, microsecond=0)
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
