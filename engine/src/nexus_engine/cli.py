"""Command-line entry point: `nexus-engine <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial

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


def _research(args: argparse.Namespace, settings: Settings) -> int:
    import json

    from .backtest.engine import run_backtest
    from .backtest.metrics import compute_metrics
    from .signals import strategy as strat
    from .signals.ledger import ensure_strategy
    from .signals.series import HIGHER_TIMEFRAME
    from .signals.service import load_candles
    from .signals.strategy import Params
    from .timeframes import tf_delta

    start, end = _date(args.start), _date(args.end)
    params = Params()
    step = tf_delta(args.tf)
    until = end + step * (params.entry_window_bars + params.horizon_bars + 2)
    span = int((until - start) / step) + params.min_bars + 50
    with connect(settings) as conn:
        pair = _pick(load_pairs(conn, load_source(conn, settings.market_source).id), args.symbol)[0]
        candles = load_candles(conn, pair.id, args.tf, until, span)
        htf_tf = HIGHER_TIMEFRAME[args.tf]
        htf = load_candles(conn, pair.id, htf_tf, until, int(span * step / tf_delta(htf_tf)) + 300) if htf_tf else None
        if args.cmd == "backtest":
            res = run_backtest(args.tf, candles, htf, start, end, params)
            print(json.dumps(compute_metrics(res.trades, res.equity_curve, res.equity_curve[0][1], start, end), indent=2))
            return 0
        from .validation.dataset import build_dataset, dataset_hash
        from .validation.registry import finish_experiment, save_validation_report, start_experiment
        from .validation.walkforward import WalkForwardConfig, run_walk_forward

        cfg = WalkForwardConfig(model=args.model)
        strategy_id = ensure_strategy(conn, strat.KEY, strat.VERSION, strat.NAME, strat.DESCRIPTION, params)
        exp = start_experiment(
            conn,
            "walk_forward",
            strategy_id,
            {"symbol": pair.symbol, "tf": args.tf, "start": args.start, "end": args.end, "walk_forward": cfg.__dict__, "lockbox": args.lockbox},
            dataset_hash(candles),
            cfg.seed,
        )
        try:
            ds = build_dataset(args.tf, candles, htf, start, end, params)
            report = run_walk_forward(ds, cfg, evaluate_lockbox=args.lockbox)
            rid = save_validation_report(conn, exp, strategy_id, start, end, report, {"costs": params.costs.as_dict(), "params": params.as_dict()})
            finish_experiment(conn, exp, {"samples": report["samples"], "out_of_sample": report["out_of_sample"], "approvable": report["approvable"]})
        except Exception as e:
            conn.rollback()
            finish_experiment(conn, exp, None, error=type(e).__name__)
            raise
        print(json.dumps({"experiment": exp, "validation_report": rid, "samples": report["samples"],
                          "out_of_sample": {k: v for k, v in report["out_of_sample"].items() if k != "reliability"},
                          "approvable": report["approvable"]}, indent=2))  # fmt: skip
        return 0


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
    ev = sub.add_parser("evaluate", help="evaluate the latest closed candle for one pair/timeframe")
    ev.add_argument("--symbol", required=True)
    ev.add_argument("--tf", required=True, choices=TIMEFRAMES)
    sub.add_parser("resolve", help="resolve outcomes of open signals")
    for name, desc in (
        ("backtest", "backtest the strategy on stored candles (prints metrics)"),
        ("validate", "walk-forward validation; records an experiment and a validation report"),
    ):
        c = sub.add_parser(name, help=desc)
        c.add_argument("--symbol", required=True)
        c.add_argument("--tf", required=True, choices=TIMEFRAMES)
        c.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
        c.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
        if name == "validate":
            c.add_argument("--model", choices=("logistic", "xgboost"), default="logistic")
            c.add_argument("--lockbox", action="store_true", help="also evaluate the lockbox (approval time only)")
    sub.add_parser("verify-chain", help="recompute the signal hash chain and report any tampering")
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

    if args.cmd in ("backtest", "validate"):
        return _research(args, settings)
    if args.cmd in ("evaluate", "resolve", "verify-chain"):
        from .signals.hashing import verify_chain
        from .signals.ledger import load_chain
        from .signals.service import SignalService

        with connect(settings) as conn:
            if args.cmd == "verify-chain":
                problems = verify_chain(load_chain(conn))
                for p in problems:
                    print(p)
                print("chain intact" if not problems else f"{len(problems)} problem(s)")
                return 0 if not problems else 1
            svc = SignalService(conn, settings)
            if args.cmd == "resolve":
                print(f"resolved {svc.resolve_open()}")
                return 0
            pairs = load_pairs(conn, load_source(conn, settings.market_source).id)
            d = svc.evaluate(_pick(pairs, args.symbol)[0], args.tf)
            print("already evaluated" if d is None else f"{d.decision} {', '.join(d.reasons)}")
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
            from .backtest.jobs import process_one
            from .paper.processor import PaperProcessor
            from .signals.service import SignalService
            from .worker import Scheduler, run_forever

            scheduler = Scheduler(
                ingestor=ingestor,
                pairs=pairs,
                close_delay=timedelta(seconds=settings.ingest_close_delay_s),
                ticker_interval=timedelta(seconds=settings.ticker_interval_s),
                signals=SignalService(conn, settings),
                jobs=partial(process_one, conn),
                paper=PaperProcessor(conn).process,
            )
            run_forever(scheduler, settings.worker_tick_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
