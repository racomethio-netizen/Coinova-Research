"""180-day forward paper test of the global strategy library (stage S5).

SIMULATED, paper only: this worker never places an order (not even a paper order) and
cannot touch real funds. Every hour it does the following.
* Loads the CLOSED 1h / 4h / 1d candles stored by the market-data worker for the trading
  pairs (Binance spot).
* Computes every library strategy's signals and records each signal at or after START.
* Replays the same library backtester (fills at the next open, fees, spread, slippage,
  stop-before-target, the owner's research risk profile) over [START, now).
* Records completed simulated trades and end-of-day equity.

The ledger is ``forward_test_events``: append-only, one row per key, so a restart never
resets or rewrites history. The 180 days are counted in calendar days from START; no
report claims more days than have elapsed.

Each strategy and timeframe has its own 10,000 USDT of paper capital.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from nexus_common.logging import get_logger
from nexus_common.timeutil import utcnow
from nexus_strategies.frames import to_frame
from nexus_strategies.library_global import HIGHER, LIBRARY, TF_SECONDS, resample, signals

from nexus_backtest.library_engine import LibResult, run

log = get_logger("nexus.library_forward")

RUN = "global-lib-2026-11-24"
START = datetime(2026, 11, 24, tzinfo=UTC)
REVIEW_DAYS = 180
INITIAL = 10_000.0
TFS = ("1h", "4h", "1d")
SHORT = {"ema_adx_macd_trend": "A", "rsi_macd_roc_momentum": "B", "donchian_breakout": "C1",
         "bollinger_squeeze_breakout": "C2", "atr_expansion_breakout": "C3",
         "bb_rsi_reversion": "D1", "vwap_deviation_reversion": "D2",
         "mtf_structure_trend": "E"}
LOAD = {"1h": 2000, "4h": 1500, "1d": 1000}
WARMUP_BARS = 400            # base bars before signals count (EMA200 settles; see S1)
WARMUP_HTF = 60              # higher-timeframe bars (EMA50)
STATUS_KEY = "nexus:forward:library:status"
EXCHANGE = "binance"


# Pre-registered backtest verdicts (docs/research/2026-11-23-global-library-result.json),
# shown next to the forward results. Only a PASS may become a live candidate, and only
# after 180 days of paper results and the owner's explicit approval.
BACKTEST: dict[str, dict[str, Any]] = {'ema_adx_macd_trend@1h': {'passed': False,
                           'trades': 1089,
                           'profit_factor': 0.8268,
                           'return_pct': -0.2421,
                           'max_drawdown': 0.2972,
                           'test_profit_factor': 0.7666,
                           'p_value': 0.9752,
                           'failed_gates': ['full_pf_ge_1.20',
                                            'test_pf_ge_1.10',
                                            'validation_pf_ge_1.00',
                                            'max_drawdown_le_15pct',
                                            'mc_p95_drawdown_le_20pct',
                                            'holm_significant',
                                            'breadth_half_symbols']},
 'rsi_macd_roc_momentum@1h': {'passed': False,
                              'trades': 2616,
                              'profit_factor': 0.7697,
                              'return_pct': -0.7751,
                              'max_drawdown': 0.8007,
                              'test_profit_factor': 0.7276,
                              'p_value': 1.0,
                              'failed_gates': ['full_pf_ge_1.20',
                                               'test_pf_ge_1.10',
                                               'validation_pf_ge_1.00',
                                               'max_drawdown_le_15pct',
                                               'mc_p95_drawdown_le_20pct',
                                               'holm_significant',
                                               'breadth_half_symbols']},
 'donchian_breakout@1h': {'passed': False,
                          'trades': 2373,
                          'profit_factor': 0.8989,
                          'return_pct': -0.3876,
                          'max_drawdown': 0.5072,
                          'test_profit_factor': 1.0532,
                          'p_value': 0.8842,
                          'failed_gates': ['full_pf_ge_1.20',
                                           'test_pf_ge_1.10',
                                           'validation_pf_ge_1.00',
                                           'max_drawdown_le_15pct',
                                           'mc_p95_drawdown_le_20pct',
                                           'holm_significant',
                                           'breadth_half_symbols']},
 'bollinger_squeeze_breakout@1h': {'passed': False,
                                   'trades': 2483,
                                   'profit_factor': 0.8435,
                                   'return_pct': -0.5727,
                                   'max_drawdown': 0.62,
                                   'test_profit_factor': 0.7619,
                                   'p_value': 0.9954,
                                   'failed_gates': ['full_pf_ge_1.20',
                                                    'test_pf_ge_1.10',
                                                    'validation_pf_ge_1.00',
                                                    'max_drawdown_le_15pct',
                                                    'mc_p95_drawdown_le_20pct',
                                                    'holm_significant',
                                                    'breadth_half_symbols']},
 'atr_expansion_breakout@1h': {'passed': False,
                               'trades': 2611,
                               'profit_factor': 0.8012,
                               'return_pct': -0.5924,
                               'max_drawdown': 0.6185,
                               'test_profit_factor': 0.821,
                               'p_value': 0.9948,
                               'failed_gates': ['full_pf_ge_1.20',
                                                'test_pf_ge_1.10',
                                                'validation_pf_ge_1.00',
                                                'max_drawdown_le_15pct',
                                                'mc_p95_drawdown_le_20pct',
                                                'holm_significant',
                                                'breadth_half_symbols']},
 'bb_rsi_reversion@1h': {'passed': False,
                         'trades': 155,
                         'profit_factor': 0.6068,
                         'return_pct': -0.0997,
                         'max_drawdown': 0.1,
                         'test_profit_factor': 0.5943,
                         'p_value': 0.978,
                         'failed_gates': ['full_pf_ge_1.20',
                                          'test_pf_ge_1.10',
                                          'holm_significant',
                                          'breadth_half_symbols']},
 'vwap_deviation_reversion@1h': {'passed': False,
                                 'trades': 379,
                                 'profit_factor': 0.6135,
                                 'return_pct': -0.2744,
                                 'max_drawdown': 0.2825,
                                 'test_profit_factor': 0.7812,
                                 'p_value': 0.999,
                                 'failed_gates': ['full_pf_ge_1.20',
                                                  'test_pf_ge_1.10',
                                                  'validation_pf_ge_1.00',
                                                  'max_drawdown_le_15pct',
                                                  'mc_p95_drawdown_le_20pct',
                                                  'holm_significant',
                                                  'breadth_half_symbols']},
 'mtf_structure_trend@1h': {'passed': False,
                            'trades': 2169,
                            'profit_factor': 0.8681,
                            'return_pct': -0.4783,
                            'max_drawdown': 0.5218,
                            'test_profit_factor': 0.8713,
                            'p_value': 0.926,
                            'failed_gates': ['full_pf_ge_1.20',
                                             'test_pf_ge_1.10',
                                             'validation_pf_ge_1.00',
                                             'max_drawdown_le_15pct',
                                             'mc_p95_drawdown_le_20pct',
                                             'holm_significant',
                                             'breadth_half_symbols']},
 'ema_adx_macd_trend@4h': {'passed': False,
                           'trades': 290,
                           'profit_factor': 1.0025,
                           'return_pct': 0.0011,
                           'max_drawdown': 0.0881,
                           'test_profit_factor': 1.0063,
                           'p_value': 0.3795,
                           'failed_gates': ['full_pf_ge_1.20',
                                            'test_pf_ge_1.10',
                                            'validation_pf_ge_1.00',
                                            'holm_significant']},
 'rsi_macd_roc_momentum@4h': {'passed': False,
                              'trades': 773,
                              'profit_factor': 0.961,
                              'return_pct': -0.0871,
                              'max_drawdown': 0.286,
                              'test_profit_factor': 0.6955,
                              'p_value': 0.5969,
                              'failed_gates': ['full_pf_ge_1.20',
                                               'test_pf_ge_1.10',
                                               'max_drawdown_le_15pct',
                                               'mc_p95_drawdown_le_20pct',
                                               'holm_significant',
                                               'breadth_half_symbols']},
 'donchian_breakout@4h': {'passed': False,
                          'trades': 616,
                          'profit_factor': 1.1569,
                          'return_pct': 0.3101,
                          'max_drawdown': 0.2505,
                          'test_profit_factor': 1.1866,
                          'p_value': 0.234,
                          'failed_gates': ['full_pf_ge_1.20',
                                           'max_drawdown_le_15pct',
                                           'holm_significant']},
 'bollinger_squeeze_breakout@4h': {'passed': False,
                                   'trades': 692,
                                   'profit_factor': 0.9178,
                                   'return_pct': -0.1341,
                                   'max_drawdown': 0.183,
                                   'test_profit_factor': 0.8725,
                                   'p_value': 0.7353,
                                   'failed_gates': ['full_pf_ge_1.20',
                                                    'test_pf_ge_1.10',
                                                    'max_drawdown_le_15pct',
                                                    'mc_p95_drawdown_le_20pct',
                                                    'holm_significant',
                                                    'breadth_half_symbols']},
 'atr_expansion_breakout@4h': {'passed': False,
                               'trades': 778,
                               'profit_factor': 1.0083,
                               'return_pct': 0.0124,
                               'max_drawdown': 0.1707,
                               'test_profit_factor': 0.8689,
                               'p_value': 0.3669,
                               'failed_gates': ['full_pf_ge_1.20',
                                                'test_pf_ge_1.10',
                                                'max_drawdown_le_15pct',
                                                'holm_significant']},
 'bb_rsi_reversion@4h': {'passed': False,
                         'trades': 45,
                         'profit_factor': 0.5763,
                         'return_pct': -0.0502,
                         'max_drawdown': 0.0742,
                         'test_profit_factor': 0.5454,
                         'p_value': 0.9514,
                         'failed_gates': ['trades',
                                          'full_pf_ge_1.20',
                                          'test_pf_ge_1.10',
                                          'validation_pf_ge_1.00',
                                          'holm_significant',
                                          'breadth_half_symbols']},
 'vwap_deviation_reversion@4h': {'passed': False,
                                 'trades': 105,
                                 'profit_factor': 0.6567,
                                 'return_pct': -0.101,
                                 'max_drawdown': 0.1481,
                                 'test_profit_factor': 1.0139,
                                 'p_value': 0.892,
                                 'failed_gates': ['full_pf_ge_1.20',
                                                  'test_pf_ge_1.10',
                                                  'validation_pf_ge_1.00',
                                                  'holm_significant',
                                                  'breadth_half_symbols']},
 'mtf_structure_trend@4h': {'passed': False,
                            'trades': 524,
                            'profit_factor': 1.0351,
                            'return_pct': 0.0483,
                            'max_drawdown': 0.1766,
                            'test_profit_factor': 0.8743,
                            'p_value': 0.2462,
                            'failed_gates': ['full_pf_ge_1.20',
                                             'test_pf_ge_1.10',
                                             'validation_pf_ge_1.00',
                                             'max_drawdown_le_15pct',
                                             'holm_significant']},
 'ema_adx_macd_trend@1d': {'passed': False,
                           'trades': 32,
                           'profit_factor': 0.5931,
                           'return_pct': -0.0203,
                           'max_drawdown': 0.0227,
                           'test_profit_factor': 0.2674,
                           'p_value': 0.9988,
                           'failed_gates': ['full_pf_ge_1.20',
                                            'test_pf_ge_1.10',
                                            'holm_significant']},
 'rsi_macd_roc_momentum@1d': {'passed': False,
                              'trades': 30,
                              'profit_factor': 2.2436,
                              'return_pct': 0.0754,
                              'max_drawdown': 0.0265,
                              'test_profit_factor': 0.7184,
                              'p_value': 0.01,
                              'failed_gates': ['test_pf_ge_1.10', 'holm_significant']},
 'donchian_breakout@1d': {'passed': False,
                          'trades': 55,
                          'profit_factor': 2.4608,
                          'return_pct': 0.2066,
                          'max_drawdown': 0.0408,
                          'test_profit_factor': 1.6667,
                          'p_value': 0.0054,
                          'failed_gates': ['holm_significant']},
 'bollinger_squeeze_breakout@1d': {'passed': False,
                                   'trades': 52,
                                   'profit_factor': 1.7203,
                                   'return_pct': 0.0821,
                                   'max_drawdown': 0.035,
                                   'test_profit_factor': 1.2956,
                                   'p_value': 0.1578,
                                   'failed_gates': ['holm_significant']},
 'atr_expansion_breakout@1d': {'passed': True,
                               'trades': 36,
                               'profit_factor': 3.4442,
                               'return_pct': 0.0755,
                               'max_drawdown': 0.0092,
                               'test_profit_factor': 4.9972,
                               'p_value': 0.0002,
                               'failed_gates': []},
 'bb_rsi_reversion@1d': {'passed': False,
                         'trades': 0,
                         'profit_factor': None,
                         'return_pct': 0.0,
                         'max_drawdown': 0.0,
                         'test_profit_factor': None,
                         'p_value': None,
                         'failed_gates': ['trades',
                                          'full_pf_ge_1.20',
                                          'test_pf_ge_1.10',
                                          'validation_pf_ge_1.00',
                                          'mc_p95_drawdown_le_20pct',
                                          'holm_significant']},
 'vwap_deviation_reversion@1d': {'passed': False,
                                 'trades': 2,
                                 'profit_factor': 0.0,
                                 'return_pct': -0.0061,
                                 'max_drawdown': 0.0079,
                                 'test_profit_factor': None,
                                 'p_value': None,
                                 'failed_gates': ['trades',
                                                  'full_pf_ge_1.20',
                                                  'test_pf_ge_1.10',
                                                  'validation_pf_ge_1.00',
                                                  'mc_p95_drawdown_le_20pct',
                                                  'holm_significant']},
 'mtf_structure_trend@1d': {'passed': False,
                            'trades': 63,
                            'profit_factor': 1.4565,
                            'return_pct': 0.068,
                            'max_drawdown': 0.0418,
                            'test_profit_factor': 0.753,
                            'p_value': 0.2178,
                            'failed_gates': ['test_pf_ge_1.10', 'holm_significant']}}


def _higher_from(tf: str, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    h = HIGHER[tf]
    return resample(frames["1d"], "1w") if h == "1w" else frames[h]


def closed(df: pd.DataFrame, tf: str, now: datetime) -> pd.DataFrame:
    """Only bars whose close time has passed (defensive: the store holds closed bars)."""
    return df[df.index + pd.Timedelta(seconds=TF_SECONDS[tf]) <= pd.Timestamp(now)]


def compute(frames: dict[str, dict[str, pd.DataFrame]], now: datetime
            ) -> dict[str, dict[str, Any]]:
    """Per ``code@tf``: warm-up state, signals since START, and the replayed result.
    ``frames[symbol][tf]`` are closed candles."""
    out: dict[str, dict[str, Any]] = {}
    end = pd.Timestamp(now)
    for tf in TFS:
        warm, base, higher = {}, {}, {}
        for sym, fr in frames.items():
            b, h = fr.get(tf), None
            if b is None or b.empty:
                warm[sym] = "no stored candles"
                continue
            h = _higher_from(tf, fr)
            if h is None or len(b) < WARMUP_BARS or len(h) < WARMUP_HTF:
                warm[sym] = (f"warming up: {len(b)} of {WARMUP_BARS} {tf} bars, "
                             f"{0 if h is None else len(h)} of {WARMUP_HTF} {HIGHER[tf]} bars")
                continue
            warm[sym] = "ready"
            base[sym], higher[sym] = b, h
        for code in LIBRARY:
            key = f"{code}@{tf}"
            sigs = {s: signals(code, base[s], tf, higher[s]) for s in base}
            fresh = {s: sg[(sg.index >= pd.Timestamp(START)) & sg.entry] for s, sg in sigs.items()}
            res = run(sigs, base, tf, start=pd.Timestamp(START), end=end) if base else None
            out[key] = {"code": code, "tf": tf, "warm": warm, "signals": fresh, "result": res}
    return out


def _events(c: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Ledger rows for one ``code@tf``: signals, completed trades and closed days."""
    code, tf = c["code"], c["tf"]
    tag = f"{SHORT[code]}@{tf}"
    rows: list[dict[str, Any]] = []
    for sym, sg in c["signals"].items():
        for t, r in sg.iterrows():
            rows.append({"symbol": sym, "kind": "signal", "key": f"{tag}|{t:%Y-%m-%dT%H:%M}",
                         "at": (t + pd.Timedelta(seconds=TF_SECONDS[tf])).to_pydatetime(),
                         "data": {"strategy": code, "tf": tf, "stop": float(r["stop"]),
                                  "target": float(r["target"]),
                                  "time_limit": int(r["time_limit"]), "simulated": True}})
    res: LibResult | None = c["result"]
    if res is None:
        return rows
    for tr in res.trades.to_dict("records"):
        et = pd.Timestamp(tr["entry_time"])
        rows.append({"symbol": tr["symbol"], "kind": "trade", "key": f"{tag}|{et:%Y-%m-%dT%H:%M}",
                     "at": pd.Timestamp(tr["exit_time"]).to_pydatetime(),
                     "data": {"strategy": code, "tf": tf, "simulated": True,
                              **{k: (str(v) if k.endswith("_time") else
                                     (float(v) if isinstance(v, (int, float)) else v))
                                 for k, v in tr.items()}}})
    eq = res.equity
    if len(eq):
        daily = eq.resample("1D").last().dropna()
        today = pd.Timestamp(now).normalize()
        for d, v in daily.items():
            if d < today:                     # only completed days
                rows.append({"symbol": "PORTFOLIO", "kind": "day",
                             "key": f"{tag}|{d:%Y-%m-%d}", "at": d.to_pydatetime(),
                             "data": {"strategy": code, "tf": tf, "equity": float(v),
                                      "simulated": True}})
    return rows


def record(factory: Any, rows: list[dict[str, Any]]) -> int:
    """Append rows; existing keys are left untouched (ON CONFLICT DO NOTHING)."""
    from nexus_db import models, transaction
    from sqlalchemy.dialects.postgresql import insert

    if not rows:
        return 0
    with transaction(factory) as s:
        stmt = insert(models.ForwardTestEvent).values(
            [{"run": RUN, **r} for r in rows]).on_conflict_do_nothing(
            index_elements=["run", "symbol", "kind", "key"]).returning(
            models.ForwardTestEvent.id)
        return len(s.execute(stmt).all())


def load(factory: Any, symbols: list[str], now: datetime) -> dict[str, dict[str, pd.DataFrame]]:
    from nexus_db import repo, transaction

    out: dict[str, dict[str, pd.DataFrame]] = {}
    with transaction(factory) as s:
        for sym in symbols:
            pid = repo.pair_id(s, EXCHANGE, sym)
            out[sym.replace("/", "")] = {
                tf: closed(to_frame(repo.load_candles(s, pid, tf, limit=LOAD[tf])), tf, now)
                for tf in TFS}
    return out


def status_view(computed: dict[str, dict[str, Any]], now: datetime,
                frames: dict[str, dict[str, pd.DataFrame]]) -> dict[str, Any]:
    out: dict[str, Any] = {"at": now.isoformat(), "run": RUN, "simulated": True,
                           "live_trading": "DISABLED", "strategies": {}, "data": {}}
    for sym, fr in frames.items():
        out["data"][sym] = {tf: {"bars": len(df), "last_bar": str(df.index[-1]) if len(df)
                                 else None} for tf, df in fr.items()}
    for key, c in computed.items():
        res: LibResult | None = c["result"]
        last = None
        for sym, sg in c["signals"].items():
            if len(sg) and (last is None or sg.index[-1] > pd.Timestamp(last["bar"])):
                last = {"symbol": sym, "bar": str(sg.index[-1]),
                        "stop": float(sg["stop"].iloc[-1]),
                        "target": float(sg["target"].iloc[-1])}
        out["strategies"][key] = {
            "warm": c["warm"], "last_signal": last,
            "open_positions": res.open_positions if res else [],
            "equity": float(res.equity.iloc[-1]) if res is not None and len(res.equity)
            else INITIAL}
    return out


async def step(factory: Any, redis: Any, symbols: list[str],
               now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    frames = await asyncio.to_thread(load, factory, symbols, now)
    gaps = []
    for sym, fr in frames.items():
        for tf, df in fr.items():
            late = pd.Timedelta(seconds=2 * TF_SECONDS[tf])
            if len(df) == 0 or pd.Timestamp(now) - (df.index[-1] + pd.Timedelta(
                    seconds=TF_SECONDS[tf])) > late:
                gaps.append({"symbol": sym, "kind": "gap",
                             "key": f"{tf}|{now:%Y-%m-%dT%H}", "at": now,
                             "data": {"tf": tf, "last_bar": str(df.index[-1]) if len(df)
                                      else None, "reason": "stale or missing candles"}})
    computed = await asyncio.to_thread(compute, frames, now)
    rows = [r for c in computed.values() for r in _events(c, now)] + gaps
    new = await asyncio.to_thread(record, factory, rows) if now >= START else 0
    status = {**status_view(computed, now, frames), "new_events": new, "gaps_now": len(gaps)}
    await redis.set(STATUS_KEY, json.dumps(status, default=str), ex=3 * 86400)
    log.info("library_forward_step", new_events=new, gaps=len(gaps))
    return status


def summary(rows: list[Any], now: datetime) -> dict[str, Any]:
    """Per strategy and timeframe: forward metrics and daily / weekly / monthly P&L from
    the ledger. Days are calendar days since START, never more than have elapsed."""
    elapsed = max(0, (now - START).days)
    out: dict[str, Any] = {"run": RUN, "started_at": START.isoformat(),
                           "review_at": (START + timedelta(days=REVIEW_DAYS)).isoformat(),
                           "days_elapsed": min(elapsed, REVIEW_DAYS) if now >= START else 0,
                           "days_required": REVIEW_DAYS, "simulated": True,
                           "complete": elapsed >= REVIEW_DAYS, "strategies": {}}
    by: dict[str, dict[str, list[Any]]] = {}
    gaps = 0
    for r in rows:
        if r.kind == "gap":
            gaps += 1
            continue
        d = r.data or {}
        key = f"{d.get('strategy')}@{d.get('tf')}"
        by.setdefault(key, {"trade": [], "day": [], "signal": []})[r.kind].append(r)
    out["gap_events"] = gaps
    for key, g in sorted(by.items()):
        tr = pd.DataFrame([{"exit_time": pd.Timestamp(x.data["exit_time"]),
                            "pnl": float(x.data["pnl"]), "fees": float(x.data["fees"]),
                            "symbol": x.symbol} for x in g["trade"]])
        days = sorted((x.key.split("|")[1], float(x.data["equity"])) for x in g["day"])
        item: dict[str, Any] = {"signals": len(g["signal"]), "trades": len(tr)}
        if len(tr):
            wins, gl = tr.pnl[tr.pnl > 0].sum(), -tr.pnl[tr.pnl <= 0].sum()
            item.update(net_pnl=float(tr.pnl.sum()), fees=float(tr.fees.sum()),
                        win_rate=float((tr.pnl > 0).mean()),
                        profit_factor=float(wins / gl) if gl > 0 else None)
            for name, rule in (("daily", "1D"), ("weekly", "W-MON"), ("monthly", "MS")):
                p = tr.set_index("exit_time").pnl.resample(rule, label="left",
                                                           closed="left").sum()
                item[name] = [{"period": str(k.date()), "pnl": float(v)}
                              for k, v in p.items()][-60:]
        if days:
            eq = pd.Series([v for _, v in days])
            curve = pd.concat([pd.Series([INITIAL]), eq])
            item.update(equity=float(eq.iloc[-1]), return_pct=float(eq.iloc[-1] / INITIAL - 1),
                        max_drawdown=float((1 - curve / curve.cummax()).max()))
        out["strategies"][key] = item
    return out


async def serve(settings: Any, stop: asyncio.Event) -> None:
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        while not stop.is_set():
            try:
                await step(factory, redis, list(settings.trading_pairs))
            except Exception as exc:
                log.error("library_forward_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            now = utcnow()
            nxt = (now + timedelta(hours=1)).replace(minute=2, second=0, microsecond=0)
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
