"""NEXUS read-only bridge for Exness MetaTrader 5.

Runs on the owner's Windows PC or VPS, next to the MT5 terminal, which the owner logs
into (a demo account first). Every few minutes it reads, from the running terminal:
* contract details;
* closed daily bars;
* closed M5 and H1 bars of the forex majors and gold (a few recent bars each time; the
  whole series when NEXUS asks for it, at most a few series per cycle);
* current quotes;
* the account summary;
* open positions.

It then sends them OUT to NEXUS over HTTPS with a bridge token.

READ-ONLY BY CONSTRUCTION: this file never calls any MetaTrader order or position
function. A NEXUS unit test checks the source for them. The MT5 password stays in the
terminal; the bridge never asks for it.

Settings (environment variables):
* NEXUS_URL: for example https://nexus-ai-fra.onrender.com (HTTPS required).
* NEXUS_BRIDGE_TOKEN: created on NEXUS > All markets > Exness bridge. Shown once; keep
  it private.
* NEXUS_SYMBOLS: optional, comma-separated base names, for example EURUSD,XAUUSD,US500.
* NEXUS_INTERVAL_SECONDS: optional, default 300.
* NEXUS_INTRADAY_SYMBOLS: optional, base names that get M5/H1 bars, default
  EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD,XAUUSD (they must also be in
  NEXUS_SYMBOLS). Set it to an empty value to send no intraday bars.
* NEXUS_SERVER_UTC_OFFSET: optional fallback for the broker's server time offset in hours,
  used when no fresh tick allows measuring it.

Run: ``py -3 nexus_mt5_bridge.py`` (see README.md in this folder).

History export, once, for research: ``py -3 nexus_mt5_bridge.py --export-history``
sends closed bars of the forex pairs in NEXUS_HISTORY_SYMBOLS (default: EURUSD, GBPUSD,
USDJPY, AUDUSD, USDCHF): M5 for the last 3 years (by month), H1 for the last 12 years
(by year) and all D1 history. Chunks NEXUS already holds are skipped, so it can be run
again safely. ``--export-history-dir FOLDER`` writes the same bars as CSV files into a
folder instead of sending them. Set MetaTrader 5's Tools > Options > Charts > "Max. bars
in chart" to Unlimited first, or the terminal returns only recent bars.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "1.1"
DEFAULT_SYMBOLS = ("EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,"
                   "XAUUSD,XAGUSD,USOIL,UKOIL,US500,USTEC,US30")
OFFSET_PROBES = ("BTCUSD", "ETHUSD")      # trade at weekends: keep the time offset measurable
FULL_HISTORY, RECENT = 1500, 10
TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}
HISTORY_SYMBOLS = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF"
HISTORY_PLAN = (("D1", 0), ("H1", 12), ("M5", 3))    # timeframe, years (0 = all)
TF_SECONDS = {"M5": 300, "H1": 3600, "D1": 86400}
INTRADAY_SYMBOLS = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD,XAUUSD"
# Bars per request: the whole stored series (M5 about 45 days, H1 about 400 days of
# trading) when NEXUS asks for it, otherwise the recent ones (3 h of M5, 6 h of H1).
INTRADAY_FULL = {"M5": 9500, "H1": 6900}
INTRADAY_RECENT = {"M5": 36, "H1": 6}
MAX_FULL_PER_CYCLE = 2                     # keeps each request well below NEXUS's size limit
FOLLOW_UP_SECONDS = 30                     # next cycle sooner while backfills are pending


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def resolve(available: list[str], wanted: list[str]) -> tuple[dict[str, str], list[str]]:
    """Base name -> broker symbol. Exness adds suffixes by account type (for example
    EURUSDm); the exact name wins, then the shortest name starting with the base."""
    out: dict[str, str] = {}
    missing = []
    for base in wanted:
        if base in available:
            out[base] = base
            continue
        cands = sorted((n for n in available if n.startswith(base) and len(n) <= len(base) + 2),
                       key=len)
        if cands:
            out[base] = cands[0]
        else:
            missing.append(base)
    return out, missing


def measure_offset(tick_times: list[int], now: float) -> int | None:
    """The broker server's offset from UTC in whole hours, from the freshest tick (server
    time, seconds). None if no tick is within 10 minutes of a whole-hour offset."""
    if not tick_times:
        return None
    diff = max(tick_times) - now
    hours = round(diff / 3600)
    if abs(hours) > 14 or abs(diff - hours * 3600) > 600:
        return None
    return int(hours)


def _num(x: Any, digits: int | None = None) -> str:
    v = float(x)
    return repr(round(v, digits)) if digits is not None else repr(v)


def collect(mt5: Any, wanted: list[str], need_history: set[str], state: dict[str, Any],
            now: float | None = None, intraday: list[str] | None = None) -> dict[str, Any]:
    """``intraday``: base names that get M5/H1 bars. The series NEXUS asked for in its
    last reply (``state["need_intraday"]``, "<name>/<TF>") are sent whole, at most
    MAX_FULL_PER_CYCLE of them."""
    now = time.time() if now is None else now
    full = set(list(state.get("need_intraday", []))[:MAX_FULL_PER_CYCLE])
    intraday_rows: list[dict[str, Any]] = []
    acct = mt5.account_info()
    if acct is None:
        raise RuntimeError(f"MT5 terminal not logged in: {mt5.last_error()}")
    available = [s.name for s in (mt5.symbols_get() or [])]
    resolved, missing = resolve(available, wanted)
    probes, _ = resolve(available, list(OFFSET_PROBES))
    ticks: list[int] = []
    symbols, bars = [], {}
    for name in list(resolved.values()) + list(probes.values()):
        mt5.symbol_select(name, True)
        tick = mt5.symbol_info_tick(name)
        if tick is not None and tick.time:
            ticks.append(int(tick.time))
    offset = measure_offset(ticks, now)
    if offset is None:
        offset = state.get("offset")
        if offset is None and os.environ.get("NEXUS_SERVER_UTC_OFFSET"):
            offset = int(os.environ["NEXUS_SERVER_UTC_OFFSET"])
    else:
        state["offset"] = offset
    for base, name in resolved.items():
        info = mt5.symbol_info(name)
        if info is None:
            missing.append(base)
            continue
        tick = mt5.symbol_info_tick(name)
        d = info.digits
        symbols.append({
            "name": name, "base_name": base, "description": str(info.description)[:128],
            "digits": d, "point": _num(info.point), "contract_size": _num(info.trade_contract_size),
            "volume_min": _num(info.volume_min), "volume_step": _num(info.volume_step),
            "volume_max": _num(info.volume_max), "swap_long": _num(info.swap_long),
            "swap_short": _num(info.swap_short), "spread_points": int(info.spread),
            "currency_base": str(info.currency_base), "currency_profit": str(info.currency_profit),
            "bid": _num(tick.bid, d) if tick is not None and tick.bid else None,
            "ask": _num(tick.ask, d) if tick is not None and tick.ask else None,
            "quote_time": int(tick.time) if tick is not None else None})
        if offset is None:
            continue                     # never send bars whose time zone is unknown
        count = FULL_HISTORY if (name in need_history or name not in state.get("sent", set())) \
            else RECENT
        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_D1, 1, count)  # 1 = skip forming bar
        if rates is not None and len(rates):
            bars[name] = [[int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]),
                           float(r["close"]), float(r["tick_volume"])] for r in rates]
            state.setdefault("sent", set()).add(name)
        if base not in (intraday or []):
            continue
        for tf in ("H1", "M5"):
            whole = f"{name}/{tf}" in full
            count = INTRADAY_FULL[tf] if whole else INTRADAY_RECENT[tf]
            rates = mt5.copy_rates_from_pos(name, getattr(mt5, f"TIMEFRAME_{tf}"), 1, count)
            if rates is not None and len(rates):
                intraday_rows.append({"name": name, "timeframe": tf, "full": whole,
                                      "rows": _rows(rates)})
    positions = [{"symbol": p.symbol, "side": "buy" if int(p.type) == 0 else "sell",
                  "volume": _num(p.volume), "price_open": _num(p.price_open),
                  "profit": _num(p.profit), "swap": _num(p.swap)}
                 for p in (mt5.positions_get() or [])]
    return {"bridge_version": VERSION, "read_only": True,
            "server_utc_offset_hours": offset if offset is not None else 0,
            "account": {"login": int(acct.login),
                        "trade_mode": TRADE_MODES.get(int(acct.trade_mode), "real"),
                        "currency": str(acct.currency), "balance": _num(acct.balance),
                        "equity": _num(acct.equity), "margin": _num(acct.margin),
                        "margin_free": _num(acct.margin_free), "leverage": int(acct.leverage),
                        "server": str(acct.server), "company": str(acct.company)},
            "symbols": symbols, "bars": bars, "positions": positions, "unresolved": missing,
            "intraday": intraday_rows}


def periods(tf: str, years: int, now: datetime) -> list[tuple[str, datetime, datetime]]:
    """(period, start, end) in broker server time: "all" for D1, a year for H1, a month
    for M5, oldest first. The current period is included (it is still growing)."""
    if tf == "D1":
        return [("all", datetime(1971, 1, 1, tzinfo=UTC), now)]
    out = []
    if tf == "H1":
        for y in range(now.year - years + 1, now.year + 1):
            out.append((f"{y:04d}", datetime(y, 1, 1, tzinfo=UTC),
                        min(datetime(y + 1, 1, 1, tzinfo=UTC), now)))
        return out
    y, m = now.year - years, now.month
    while (y, m) <= (now.year, now.month):
        nxt = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((f"{y:04d}-{m:02d}", datetime(y, m, 1, tzinfo=UTC),
                    min(datetime(nxt[0], nxt[1], 1, tzinfo=UTC), now)))
        y, m = nxt
    return out


def _period_end(period: str) -> datetime:
    if len(period) == 4:
        return datetime(int(period) + 1, 1, 1, tzinfo=UTC)
    y, m = int(period[:4]), int(period[5:])
    return datetime(y + 1, 1, 1, tzinfo=UTC) if m == 12 else datetime(y, m + 1, 1, tzinfo=UTC)


def _complete(period: str, tf: str, now: datetime) -> bool:
    """A period that ended more than a day ago will not change any more (D1 "all" always
    grows, so it is always re-sent)."""
    return tf != "D1" and _period_end(period).timestamp() < now.timestamp() - 86400


def _rows(rates: Any) -> list[list[float]]:
    return [[int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r["tick_volume"])] for r in rates]


def export_history(mt5: Any, send: Callable[[str, dict[str, Any]], dict[str, Any]],
                   symbols: list[str], offset: int, *, now: float | None = None,
                   sleep: Callable[[float], None] = time.sleep, retries: int = 3,
                   out_dir: Path | None = None) -> dict[str, int]:
    """Export closed bars chunk by chunk. ``send(path, payload)`` posts to NEXUS; with
    ``out_dir`` the bars are written to ``<PAIR>_<TF>.csv`` files (UTC) instead.
    READ-ONLY: only symbol and rate reads."""
    now_server = datetime.fromtimestamp((time.time() if now is None else now) + offset * 3600,
                                        tz=UTC)
    available = [s.name for s in (mt5.symbols_get() or [])]
    resolved, missing = resolve(available, symbols)
    for base in missing:
        log(f"history: {base} is not offered by this account; skipped")
    have: set[tuple[str, str, str]] = set()
    if out_dir is None:
        man = send("/api/v1/bridge/mt5/history/manifest", {})
        have = {(c["symbol"], c["timeframe"], c["period"]) for c in man.get("chunks", [])}
    stats = {"sent": 0, "skipped": 0, "empty": 0, "bars": 0}
    files: dict[tuple[str, str], dict[int, str]] = {}
    for base, name in resolved.items():
        mt5.symbol_select(name, True)
        for tf, years in HISTORY_PLAN:
            code = getattr(mt5, f"TIMEFRAME_{tf}")
            for period, start, end in periods(tf, years, now_server):
                if (base, tf, period) in have and _complete(period, tf, now_server):
                    stats["skipped"] += 1
                    continue
                rates = None
                for attempt in range(retries):
                    rates = mt5.copy_rates_range(name, code, start, end)
                    if rates is not None and len(rates):
                        break
                    sleep(5 * (attempt + 1))          # the terminal may still be downloading
                if rates is None or not len(rates):
                    stats["empty"] += 1
                    continue
                rows = _rows(rates)
                if out_dir is not None:
                    series = files.setdefault((base, tf), {})
                    for r in rows:
                        t = int(r[0]) - offset * 3600
                        if t + TF_SECONDS[tf] <= now_server.timestamp() - offset * 3600:
                            series[t] = (f"{datetime.fromtimestamp(t, tz=UTC):%Y-%m-%dT%H:%M:%SZ}"
                                         f",{r[1]!r},{r[2]!r},{r[3]!r},{r[4]!r},{int(r[5])}")
                    stats["bars"] += len(rows)
                    continue
                resp = send("/api/v1/bridge/mt5/history", {
                    "bridge_version": VERSION, "read_only": True,
                    "server_utc_offset_hours": offset, "symbol": name, "base_name": base,
                    "timeframe": tf, "period": period, "rows": rows})
                stats["sent"] += 1
                stats["bars"] += int(resp.get("bars_stored", 0))
                log(f"history: {base} {tf} {period}: {resp.get('bars_stored')} bars stored")
                sleep(0.5)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for (base, tf), series in files.items():
            (out_dir / f"{base}_{tf}.csv").write_text(
                "time,open,high,low,close,volume\n" + "\n".join(series[k] for k in sorted(series))
                + "\n")
    return stats


def post(url: str, token: str, payload: dict[str, Any], timeout: int = 60,
         path: str = "/api/v1/bridge/mt5/snapshot") -> dict[str, Any]:
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError("NEXUS_URL must use https:// so the token is never sent in clear")
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        url.rstrip("/") + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "User-Agent": f"nexus-mt5-bridge/{VERSION}"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        out: dict[str, Any] = json.loads(r.read().decode())
        return out


def main() -> None:  # pragma: no cover - needs Windows and a running MT5 terminal
    import MetaTrader5 as mt5  # Windows only

    url, token = os.environ.get("NEXUS_URL", ""), os.environ.get("NEXUS_BRIDGE_TOKEN", "")
    args = sys.argv[1:]
    if "--export-history" in args or "--export-history-dir" in args:
        out_dir = Path(args[args.index("--export-history-dir") + 1]) \
            if "--export-history-dir" in args else None
        if out_dir is None and (not url or not token):
            sys.exit("set NEXUS_URL and NEXUS_BRIDGE_TOKEN first (see README.md)")
        if not mt5.initialize():
            sys.exit(f"cannot attach to the MT5 terminal: {mt5.last_error()}")
        probe: dict[str, Any] = {}
        wanted_fx = [s.strip().upper() for s in os.environ.get(
            "NEXUS_HISTORY_SYMBOLS", HISTORY_SYMBOLS).split(",") if s.strip()]
        collect(mt5, wanted_fx, set(), probe)          # measures the server time offset
        offset = probe.get("offset")
        if offset is None and os.environ.get("NEXUS_SERVER_UTC_OFFSET"):
            offset = int(os.environ["NEXUS_SERVER_UTC_OFFSET"])
        if offset is None:
            sys.exit("cannot measure the broker's server time offset now (market closed?); "
                     "set NEXUS_SERVER_UTC_OFFSET or run it while the market is open")
        log(f"history export for {', '.join(wanted_fx)} (server time UTC{offset:+d})")
        stats = export_history(mt5, lambda p, body: post(url, token, body, timeout=120,
                                                         path=p),
                               wanted_fx, offset, out_dir=out_dir)
        log(f"history export finished: {stats}")
        return
    if not url or not token:
        sys.exit("set NEXUS_URL and NEXUS_BRIDGE_TOKEN first (see README.md)")
    wanted = [s.strip().upper() for s in os.environ.get("NEXUS_SYMBOLS", DEFAULT_SYMBOLS)
              .split(",") if s.strip()]
    interval = max(60, int(os.environ.get("NEXUS_INTERVAL_SECONDS", "300")))
    intraday = [s.strip().upper() for s in os.environ.get(
        "NEXUS_INTRADAY_SYMBOLS", INTRADAY_SYMBOLS).split(",") if s.strip()]
    state: dict[str, Any] = {}
    need: set[str] = set()
    log(f"NEXUS MT5 bridge {VERSION} (read-only) for {len(wanted)} symbols")
    while True:
        try:
            if not mt5.initialize():
                raise RuntimeError(f"cannot attach to the MT5 terminal: {mt5.last_error()}")
            payload = collect(mt5, wanted, need, state, intraday=intraday)
            resp = post(url, token, payload, timeout=120)
            need = set(resp.get("need_history", []))
            state["need_intraday"] = list(resp.get("need_intraday", []))
            log(f"sent {len(payload['symbols'])} symbols, {len(payload['bars'])} with bars "
                f"({payload['account']['trade_mode']} account); NEXUS wrote "
                f"{resp.get('bars_written')} daily and {resp.get('intraday_written', 0)} "
                f"intraday bars; {len(state['need_intraday'])} series still to send")
        except Exception as exc:  # keep running; NEXUS shows "not connected" if it stops
            log(f"error: {type(exc).__name__}: {str(exc)[:200]}")
        time.sleep(FOLLOW_UP_SECONDS if state.get("need_intraday") else interval)


if __name__ == "__main__":  # pragma: no cover
    main()
