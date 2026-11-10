"""Command-line entry point: `nexus-engine <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx

from .config import ConfigError, Settings, get_settings
from .db import connect
from .logs import log, setup_logging
from .marketdata import archive
from .marketdata.binance import BinancePublicClient
from .marketdata.ingest import Ingestor, Pair, load_pairs, load_source
from .marketdata.ratelimit import TokenBucket
from .timeframes import TIMEFRAMES

logger = logging.getLogger("nexus.cli")


def build_client(settings: Settings) -> BinancePublicClient:
    if settings.market_source != "binance_spot":
        raise ConfigError(f"MARKET_SOURCE: no adapter for {settings.market_source!r}")
    rate = settings.market_data_requests_per_minute / 60
    return BinancePublicClient(
        settings.binance_base_url,
        timeout_s=settings.market_data_timeout_s,
        limiter=TokenBucket(rate_per_s=rate, capacity=max(2.0, rate * 4)),
    )


def _pick(pairs: list[Pair], symbol: str | None) -> list[Pair]:
    chosen = [p for p in pairs if symbol is None or p.symbol == symbol]
    if not chosen:
        raise SystemExit(f"unknown or disabled pair: {symbol}")
    return chosen


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC) if "T" not in s else datetime.fromisoformat(s).astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate configuration and print the compliance gates")
    ing = sub.add_parser("ingest", help="one incremental pass (all pairs/timeframes) plus gap retries")
    ing.add_argument("--symbol")
    bf = sub.add_parser("backfill", help="fetch a historical range from the REST API")
    bf.add_argument("--symbol", required=True)
    bf.add_argument("--tf", required=True, choices=TIMEFRAMES)
    bf.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    bf.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    ar = sub.add_parser("archive-backfill", help="import checksum-verified monthly archives")
    ar.add_argument("--symbol", required=True)
    ar.add_argument("--tf", required=True, choices=TIMEFRAMES)
    ar.add_argument("--from-month", required=True, help="YYYY-MM")
    ar.add_argument("--to-month", required=True, help="YYYY-MM")
    sub.add_parser("worker", help="run the scheduler forever")
    sv = sub.add_parser("serve", help="run the FastAPI app (/health, /internal/*)")
    sv.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    setup_logging(settings.log_level)

    if args.cmd == "check":
        print(
            f"APP_ENV={settings.app_env} MARKET_SOURCE={settings.market_source} SIGNALS_VISIBILITY={settings.signals_visibility} "
            f"PAID_SIGNALS_ENABLED={settings.paid_signals_enabled} LIVE_EXECUTION_ENABLED=false "
            f"DATABASE_URL={'set' if settings.database_url else 'not set'}"
        )
        return 0
    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port, log_config=None)
        return 0

    client = build_client(settings)
    with connect(settings) as conn:
        source = load_source(conn, settings.market_source)
        ingestor = Ingestor(conn, source, client, settings)
        pairs = load_pairs(conn, source.id)
        if args.cmd == "ingest":
            for pair in _pick(pairs, args.symbol):
                for tf in pair.timeframes:
                    ingestor.ingest_incremental(pair, tf)
                    ingestor.resolve_gaps(pair, tf)
                ingestor.update_price(pair)
            return 0
        if args.cmd == "backfill":
            pair = _pick(pairs, args.symbol)[0]
            r = ingestor.backfill(pair, args.tf, _date(args.start), _date(args.end))
            log(logger, logging.INFO, "backfill_done", upserted=r.upserted, rejected=r.rejected, gaps=r.gaps_recorded)
            return 0
        if args.cmd == "archive-backfill":
            pair = _pick(pairs, args.symbol)[0]
            y, m = map(int, args.from_month.split("-"))
            ey, em = map(int, args.to_month.split("-"))
            with httpx.Client(timeout=60) as http:
                while (y, m) <= (ey, em):
                    candles = archive.fetch_month(http, settings.binance_archive_url, pair.symbol, args.tf, y, m)
                    r = ingestor.import_candles(pair, args.tf, candles)
                    log(logger, logging.INFO, "archive_month", month=f"{y:04d}-{m:02d}", upserted=r.upserted, rejected=r.rejected)
                    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            return 0
        if args.cmd == "worker":
            from .worker import Scheduler, run_forever

            scheduler = Scheduler(
                ingestor=ingestor,
                pairs=pairs,
                close_delay=timedelta(seconds=settings.ingest_close_delay_s),
                ticker_interval=timedelta(seconds=settings.ticker_interval_s),
            )
            run_forever(scheduler, settings.worker_tick_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
