"""Multi-asset research worker end to end on Postgres (downloads replaced by fixed data):
it honours each market's switch, appends the NASDAQ-100 paper ledger once per day without
rewriting, and reports failed downloads instead of estimating."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import multiasset_paper as mp
from nexus_db import make_session_factory, models, repo, transaction
from sqlalchemy import func, select

from tests.integration.conftest import truncate
from tests.memredis import MemRedis


def _series(start: str, n: int, level: float, seed: int) -> pd.Series:
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(level * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))), index=idx)


@pytest.fixture
def setup(migrated_engine: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    truncate(migrated_engine, "forward_test_events, audit_logs")
    factory = make_session_factory(migrated_engine)
    calls: list[str] = []

    async def fred(_client: Any, sid: str, _since: Any) -> pd.Series:
        calls.append(sid)
        if sid.startswith("IR3TIB") or sid == "DTB3":
            return pd.Series([3.0, 3.1], index=pd.to_datetime(["2026-10-01", "2026-11-01"]))
        if sid == "DEXSZUS":
            raise RuntimeError("FRED unavailable")          # one failed download
        return _series("2025-06-02", 420, 20_000.0 if sid == "NASDAQ100" else 1.1, len(sid))

    async def perp(_client: Any, _redis: Any, sym: str, _now: Any) -> pd.Series:
        calls.append(sym)
        return _series("2025-12-01", 120, 100.0, len(sym))

    monkeypatch.setattr(mp, "fetch_fred", fred)
    monkeypatch.setattr(mp, "fetch_perp", perp)
    yield factory, MemRedis(), calls
    truncate(migrated_engine, "forward_test_events, audit_logs")   # leave nothing behind


def _set(factory: Any, market: str, autonomy: str) -> None:
    with transaction(factory) as s:
        repo.record_audit(s, actor="user:1", action="market_autonomy_set", target_type="market",
                          target_id=market, details={"autonomy": autonomy})


def _days(factory: Any) -> int:
    with transaction(factory) as s:
        return int(s.scalar(select(func.count()).select_from(models.ForwardTestEvent).where(
            models.ForwardTestEvent.run == mp.RUN, models.ForwardTestEvent.kind == "day")))


async def test_defaults_research_everything_and_paper_the_ndx_rule(setup: Any) -> None:
    factory, redis, _ = setup
    st = await mp.step(factory, redis, None)  # type: ignore[arg-type]
    m = st["markets"]
    assert m["stocks"]["autonomy"] == "PAPER" and m["forex"]["autonomy"] == "RESEARCH"
    assert m["commodities"]["autonomy"] == "RESEARCH"
    syms = {i["symbol"] for i in m["stocks"]["instruments"]}
    assert "NDX" in syms and "QQQUSDT" in syms
    assert len(m["commodities"]["instruments"]) == len(mp.COMMODITY_PERPS)
    assert any("CHF" in e for e in m["forex"]["errors"])      # reported, not estimated
    assert len(m["forex"]["instruments"]) == 6 and "carry" in m["forex"]["instruments"][0]
    n = _days(factory)
    assert n > 0 and m["stocks"]["forward"]["new_days"] == n
    again = await mp.step(factory, redis, None)  # type: ignore[arg-type]
    assert again["markets"]["stocks"]["forward"] == {"new_days": 0, "divergences": 0}
    assert _days(factory) == n                                # never rewritten
    assert json.loads(await redis.get(mp.STATUS_KEY))["paper"] is True


async def test_switches_are_obeyed(setup: Any) -> None:
    factory, redis, calls = setup
    _set(factory, "stocks", "RESEARCH")
    _set(factory, "forex", "OFF")
    _set(factory, "commodities", "OFF")
    st = await mp.step(factory, redis, None)  # type: ignore[arg-type]
    m = st["markets"]
    assert "forward" not in m["stocks"] and _days(factory) == 0   # research: no ledger
    assert m["forex"] == {"autonomy": "OFF"} and m["commodities"] == {"autonomy": "OFF"}
    assert not any(c.startswith("DEX") or c in mp.COMMODITY_PERPS for c in calls)
    _set(factory, "stocks", "OFF")
    calls.clear()
    st = await mp.step(factory, redis, None)  # type: ignore[arg-type]
    assert st["markets"]["stocks"] == {"autonomy": "OFF"} and calls == []


async def test_exness_bars_join_the_research_view(setup: Any, migrated_engine: Any) -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    factory, redis, _ = setup
    with transaction(factory) as s:
        ex = models.Exchange(id=99, code="exness", name="Exness (MetaTrader 5)",
                             is_enabled=False, capabilities={})
        s.add(ex)
        s.flush()
        pid = repo.upsert_trading_pair(
            s, exchange_id=99, symbol="XAUUSD", exchange_symbol="XAUUSDm", base="XAU",
            quote="USD", price_tick=Decimal("0.01"), amount_step=Decimal("0.01"),
            min_amount=Decimal("0.01"), min_notional=None, maker_fee=None, taker_fee=None,
            synced_at=datetime.now(UTC))
        s.get(models.TradingPair, pid).asset_class = "commodities"
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        repo.upsert_candles(s, pid, "1d", [
            repo.CandleRow(t0 + timedelta(days=i), Decimal(3000 + i), Decimal(3010 + i),
                           Decimal(2990 + i), Decimal(3005 + i), Decimal(1)) for i in range(60)],
            source="mt5")
    try:
        st = await mp.step(factory, redis, None)  # type: ignore[arg-type]
        rows = [i for i in st["markets"]["commodities"]["instruments"]
                if i["symbol"] == "EXNESS:XAUUSD"]
        assert rows and rows[0]["last"] == 3064.0 and rows[0]["name"] == "XAUUSD (Exness XAUUSDm)"
        assert not [i for i in st["markets"]["forex"]["instruments"]
                    if i["symbol"].startswith("EXNESS:")]
    finally:
        from sqlalchemy import text
        with migrated_engine.begin() as c:
            c.execute(text("DELETE FROM market_candles WHERE source = 'mt5'"))
            c.execute(text("DELETE FROM trading_pairs WHERE exchange_id = 99"))
            c.execute(text("DELETE FROM exchanges WHERE id = 99"))
