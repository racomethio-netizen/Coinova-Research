"""NEXUS read-only bridge for Exness MetaTrader 5.

Runs on the owner's Windows PC or VPS, next to the MT5 terminal, which the owner logs
into (a demo account first). Every few minutes it reads, from the running terminal:
* contract details;
* closed daily bars;
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
* NEXUS_SERVER_UTC_OFFSET: optional fallback for the broker's server time offset in hours,
  used when no fresh tick allows measuring it.

Run: ``py -3 nexus_mt5_bridge.py`` (see README.md in this folder).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any

VERSION = "1.0"
DEFAULT_SYMBOLS = ("EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,"
                   "XAUUSD,XAGUSD,USOIL,UKOIL,US500,USTEC,US30")
OFFSET_PROBES = ("BTCUSD", "ETHUSD")      # trade at weekends: keep the time offset measurable
FULL_HISTORY, RECENT = 1500, 10
TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}


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
            now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
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
            "symbols": symbols, "bars": bars, "positions": positions, "unresolved": missing}


def post(url: str, token: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError("NEXUS_URL must use https:// so the token is never sent in clear")
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        url.rstrip("/") + "/api/v1/bridge/mt5/snapshot", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "User-Agent": f"nexus-mt5-bridge/{VERSION}"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        out: dict[str, Any] = json.loads(r.read().decode())
        return out


def main() -> None:  # pragma: no cover - needs Windows and a running MT5 terminal
    import MetaTrader5 as mt5  # Windows only

    url, token = os.environ.get("NEXUS_URL", ""), os.environ.get("NEXUS_BRIDGE_TOKEN", "")
    if not url or not token:
        sys.exit("set NEXUS_URL and NEXUS_BRIDGE_TOKEN first (see README.md)")
    wanted = [s.strip().upper() for s in os.environ.get("NEXUS_SYMBOLS", DEFAULT_SYMBOLS)
              .split(",") if s.strip()]
    interval = max(60, int(os.environ.get("NEXUS_INTERVAL_SECONDS", "300")))
    state: dict[str, Any] = {}
    need: set[str] = set()
    log(f"NEXUS MT5 bridge {VERSION} (read-only) for {len(wanted)} symbols")
    while True:
        try:
            if not mt5.initialize():
                raise RuntimeError(f"cannot attach to the MT5 terminal: {mt5.last_error()}")
            payload = collect(mt5, wanted, need, state)
            resp = post(url, token, payload)
            need = set(resp.get("need_history", []))
            log(f"sent {len(payload['symbols'])} symbols, {len(payload['bars'])} with bars "
                f"({payload['account']['trade_mode']} account); NEXUS wrote "
                f"{resp.get('bars_written')} bars")
        except Exception as exc:  # keep running; NEXUS shows "not connected" if it stops
            log(f"error: {type(exc).__name__}: {str(exc)[:200]}")
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
