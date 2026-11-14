"""``python -m nexus_market.cli backfill --exchange binance --days 730 [--timeframes 5m,1h]``

One-off historical backfill for research/backtesting (real exchange data only)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from nexus_common.logging import configure_logging, get_logger
from nexus_common.timeutil import utcnow
from nexus_config import ExchangeCode, Timeframe, get_settings
from nexus_db import make_engine, make_session_factory
from nexus_exchange.factory import build_from_settings

from nexus_market.ingest import backfill, sync_pairs

log = get_logger("nexus.market.cli")


async def run_backfill(exchange: str, days: int, timeframes: list[Timeframe],
                       symbols: list[str]) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    adapter = build_from_settings(ExchangeCode(exchange), settings)
    try:
        pairs = await sync_pairs(adapter, factory, symbols)
        start = utcnow() - timedelta(days=days)
        for symbol, pid in pairs.items():
            for tf in timeframes:
                res = await backfill(adapter, factory, pair_id=pid, symbol=symbol, timeframe=tf,
                                     start=start, max_pages=10_000, from_start=True)
                log.info("backfill_done", exchange=exchange, symbol=symbol, timeframe=tf.value,
                         stored=res.stored, pages=res.pages, gaps=len(res.gaps),
                         missing=sum(g[2] for g in res.gaps))
    finally:
        await adapter.close()
        engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(prog="nexus-market")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--exchange", required=True, choices=[e.value for e in ExchangeCode])
    b.add_argument("--days", type=int, required=True)
    b.add_argument("--timeframes", default="5m,15m,1h,4h")
    b.add_argument("--symbols", default=None)
    args = p.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    tfs = [Timeframe(t) for t in args.timeframes.split(",")]
    symbols = args.symbols.split(",") if args.symbols else settings.trading_pairs
    asyncio.run(run_backfill(args.exchange, args.days, tfs, symbols))


if __name__ == "__main__":
    main()
