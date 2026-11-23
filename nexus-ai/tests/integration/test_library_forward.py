"""Library forward paper test (S5) on real PostgreSQL and Redis: signals, simulated trades
and days are appended once; a second run (or a restart) adds nothing; days are counted
honestly; no order of any kind is created."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import numpy as np
import pandas as pd
import pytest
import redis.asyncio as aioredis
from nexus_backtest import library_forward as lf
from nexus_common.timeutil import utcnow
from nexus_db import make_session_factory, models, repo, transaction
from sqlalchemy import Engine, func, select

from tests.integration.conftest import truncate

pytestmark = [pytest.mark.db, pytest.mark.redis]


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    url = os.environ.get("NEXUS_TEST_REDIS_URL")
    if not url:
        pytest.skip("NEXUS_TEST_REDIS_URL not set")
    r = aioredis.from_url(url)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def _rows(n: int, freq: str, end: pd.Timestamp, seed: int) -> list[repo.CandleRow]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    drift = np.repeat(rng.choice([-0.003, 0.0, 0.004], size=n // 40 + 1), 40)[:n]
    close = 60_000 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    vol = rng.lognormal(6, 0.7, n)
    return [repo.CandleRow(t.to_pydatetime(), D(f"{o:.2f}"), D(f"{h:.2f}"), D(f"{lo:.2f}"),
                           D(f"{c:.2f}"), D(f"{v:.4f}"))
            for t, o, h, lo, c, v in zip(idx, open_, high, low, close, vol, strict=True)]


@pytest.fixture
def factory(migrated_engine: Engine) -> Any:
    f = make_session_factory(migrated_engine)
    yield f
    truncate(migrated_engine, "forward_test_events, market_candles, trading_pairs, orders")


async def test_forward_ledger_is_append_only_and_honest(factory: Any, redis: Any) -> None:
    now = lf.START + timedelta(days=30, hours=5, minutes=3)
    last = pd.Timestamp(now).floor("1h")
    with transaction(factory) as s:
        repo.upsert_trading_pair(s, exchange_id=repo.exchange_id(s, "binance"),
                                 symbol="BTC/USDT", exchange_symbol="BTCUSDT", base="BTC",
                                 quote="USDT", price_tick=D("0.01"), amount_step=D("0.00001"),
                                 min_amount=None, min_notional=D(5), maker_fee=None,
                                 taker_fee=None, synced_at=utcnow())
        pid = repo.pair_id(s, "binance", "BTC/USDT")
        repo.upsert_candles(s, pid, "1h", _rows(1500, "1h", last - pd.Timedelta(hours=1), 1))
        repo.upsert_candles(s, pid, "4h", _rows(900, "4h", last.floor("4h") - pd.Timedelta(
            hours=4), 2))
        repo.upsert_candles(s, pid, "1d", _rows(700, "1D", last.floor("1D") - pd.Timedelta(
            days=1), 3))

    status = await lf.step(factory, redis, ["BTC/USDT"], now)
    assert status["simulated"] and status["live_trading"] == "DISABLED"
    assert status["new_events"] > 0
    warm = status["strategies"]["donchian_breakout@1h"]["warm"]
    assert warm == {"BTCUSDT": "ready"}
    with transaction(factory) as s:
        kinds = dict(s.execute(select(models.ForwardTestEvent.kind, func.count())
                               .group_by(models.ForwardTestEvent.kind)).all())
        first = s.scalar(select(func.count()).select_from(models.ForwardTestEvent))
        assert kinds.get("signal", 0) > 0 and kinds.get("day", 0) > 0
        trades = s.scalars(select(models.ForwardTestEvent).where(
            models.ForwardTestEvent.kind == "trade")).all()
        assert all(t.data["simulated"] is True for t in trades)
        # Nothing before START is recorded, and no order of any kind exists.
        assert s.scalar(select(func.min(models.ForwardTestEvent.at))) >= lf.START
        assert s.scalar(select(func.count()).select_from(models.Order)) == 0

    # A restart / the next run over the same candles appends nothing and rewrites nothing.
    again = await lf.step(factory, redis, ["BTC/USDT"], now)
    assert again["new_events"] == 0
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.ForwardTestEvent)) == first
        rows = s.scalars(select(models.ForwardTestEvent)).all()
    view = lf.summary(list(rows), now)
    assert view["days_elapsed"] == 30 and view["days_required"] == 180
    assert view["complete"] is False
    some = next(v for v in view["strategies"].values() if v.get("trades"))
    assert {"daily", "weekly", "monthly"} <= set(some)
    assert json.loads(await redis.get(lf.STATUS_KEY))["run"] == lf.RUN


async def test_warm_up_and_stale_data_are_reported(factory: Any, redis: Any) -> None:
    now = lf.START + timedelta(days=2)
    with transaction(factory) as s:
        repo.upsert_trading_pair(s, exchange_id=repo.exchange_id(s, "binance"),
                                 symbol="BTC/USDT", exchange_symbol="BTCUSDT", base="BTC",
                                 quote="USDT", price_tick=D("0.01"), amount_step=D("0.00001"),
                                 min_amount=None, min_notional=D(5), maker_fee=None,
                                 taker_fee=None, synced_at=utcnow())
        pid = repo.pair_id(s, "binance", "BTC/USDT")
        # Only 100 hourly bars that ended a day ago: not warm, and stale.
        repo.upsert_candles(s, pid, "1h", _rows(100, "1h", pd.Timestamp(now) - pd.Timedelta(
            days=1), 4))
    status = await lf.step(factory, redis, ["BTC/USDT"], now)
    w = status["strategies"]["ema_adx_macd_trend@1h"]["warm"]["BTCUSDT"]
    assert w.startswith("warming up: 100 of 400")
    assert status["gaps_now"] == 3                  # 1h stale, 4h and 1d missing
    with transaction(factory) as s:
        assert s.scalar(select(func.count()).select_from(models.ForwardTestEvent).where(
            models.ForwardTestEvent.kind == "gap")) == 3
