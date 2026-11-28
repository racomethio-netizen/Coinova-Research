"""NEXUS demo trader for Exness MetaTrader 5: places NEXUS's orders on a DEMO account.

It is separate from the read-only bridge (``nexus_mt5_bridge.py``), which never trades.
It runs on the owner's Windows PC or VPS, next to an MT5 terminal logged in to an Exness
**demo** account.

Every couple of minutes it does three things:
1. **Sends NEXUS** the demo account, XAUUSD and EURUSD quotes, closed D1 bars (XAUUSD),
   closed H1 bars (EURUSD), NEXUS's own positions, and the results of the last orders.
2. **Receives instructions:** open, close, or modify a stop.
3. **Carries them out** in MT5, marked with NEXUS's magic number and a client ID.

**Safety:**
* **Demo only.** Before every sync and every order it checks
  ``account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO``. On any other account it
  refuses and stops. NEXUS also refuses non-demo accounts (HTTP 403). No setting
  overrides this.
* **No duplicates.** An order whose client ID already appears in the terminal's order
  history (30 days) is never sent again. A failed or timed-out order is not retried in
  the same pass; NEXUS re-decides from the real positions on the next sync.
* **What it never touches:** no withdrawals, no password (the MT5 password stays in the
  terminal), and only positions carrying NEXUS's magic number.

Settings: ``NEXUS_URL``, ``NEXUS_BRIDGE_TOKEN``, ``NEXUS_DEMO_INTERVAL_SECONDS`` (default
120).

Run: ``py -3 nexus_mt5_demo_trader.py`` (see README.md).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_mt5_bridge import log, measure_offset, post, resolve

VERSION = "1.1"
MAGIC = 26112701
SYMBOLS = ("XAUUSD", "EURUSD")
# Quotes and contract details only (no bars), so NEXUS can place orders approved by its
# orchestrator and risk engine on these pairs too. Skipped if the account does not offer
# them.
EXTRA = ("GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD")
BARS = {"XAUUSD": ("TIMEFRAME_D1", "D1", 80), "EURUSD": ("TIMEFRAME_H1", "H1", 1100)}
DEVIATION_POINTS = 20
OK = 10009                                            # TRADE_RETCODE_DONE


class NotDemo(RuntimeError):
    pass


def guard_demo(mt5: Any) -> Any:
    """The account, or NotDemo if it is not a demo account (checked every time)."""
    acct = mt5.account_info()
    if acct is None:
        raise RuntimeError(f"MT5 terminal not logged in: {mt5.last_error()}")
    if int(acct.trade_mode) != int(mt5.ACCOUNT_TRADE_MODE_DEMO):
        raise NotDemo("REFUSED: this MT5 account is not a DEMO account. The NEXUS demo "
                      "trader only ever trades demo accounts.")
    return acct


def collect(mt5: Any, state: dict[str, Any], results: list[dict[str, Any]],
            now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    acct = guard_demo(mt5)
    available = [s.name for s in (mt5.symbols_get() or [])]
    resolved, missing = resolve(available, list(SYMBOLS))
    if missing:
        raise RuntimeError(f"symbols not offered on this account: {missing}")
    extra, _ = resolve(available, list(EXTRA))
    symbols, bars, ticks = [], {}, []
    for base, name in {**resolved, **extra}.items():
        mt5.symbol_select(name, True)
        info, tick = mt5.symbol_info(name), mt5.symbol_info_tick(name)
        if info is None or tick is None or not tick.bid or not tick.ask:
            raise RuntimeError(f"no quote for {name}")
        ticks.append(int(tick.time))
        symbols.append({"name": name, "base_name": base, "point": float(info.point),
                        "contract_size": float(info.trade_contract_size),
                        "volume_min": float(info.volume_min),
                        "volume_step": float(info.volume_step),
                        "volume_max": float(info.volume_max), "bid": float(tick.bid),
                        "ask": float(tick.ask)})
        if base not in BARS:
            continue
        tf_attr, tf, count = BARS[base]
        rates = mt5.copy_rates_from_pos(name, getattr(mt5, tf_attr), 1, count)  # closed bars
        bars[name] = {tf: [[int(r["time"]), float(r["open"]), float(r["high"]),
                            float(r["low"]), float(r["close"])] for r in (rates if rates
                                                                         is not None else [])]}
    offset = measure_offset(ticks, now)
    if offset is None:
        offset = state.get("offset")
    if offset is None:
        raise RuntimeError("cannot measure the broker's server time offset (market closed?)")
    state["offset"] = offset
    positions = [{"ticket": int(p.ticket), "symbol": p.symbol,
                  "side": "buy" if int(p.type) == 0 else "sell", "volume": float(p.volume),
                  "price_open": float(p.price_open), "sl": float(p.sl), "tp": float(p.tp),
                  "time": int(p.time), "magic": int(p.magic), "comment": str(p.comment)[:64]}
                 for p in (mt5.positions_get() or []) if int(p.magic) == MAGIC]
    return {"bridge_version": VERSION, "server_utc_offset_hours": offset,
            "account": {"login": int(acct.login), "trade_mode": "demo",
                        "currency": str(acct.currency), "balance": float(acct.balance),
                        "equity": float(acct.equity), "margin": float(acct.margin),
                        "margin_free": float(acct.margin_free), "leverage": int(acct.leverage),
                        "server": str(acct.server), "company": str(acct.company)},
            "symbols": symbols, "positions": positions, "bars": bars, "results": results}


def already_sent(mt5: Any, client_id: str, now: float) -> bool:
    start = datetime.fromtimestamp(now, tz=UTC) - timedelta(days=30)
    end = datetime.fromtimestamp(now, tz=UTC) + timedelta(days=1)
    for o in (mt5.history_orders_get(start, end) or []):
        if str(o.comment) == client_id:
            return True
    return any(str(o.comment) == client_id for o in (mt5.orders_get() or []))


def filling(mt5: Any, info: Any) -> int:
    mode = int(getattr(info, "filling_mode", 0))
    if mode & 1:
        return int(mt5.ORDER_FILLING_FOK)
    if mode & 2:
        return int(mt5.ORDER_FILLING_IOC)
    return int(mt5.ORDER_FILLING_RETURN)


def execute(mt5: Any, instructions: list[dict[str, Any]], now: float | None = None
            ) -> list[dict[str, Any]]:
    """Carry out NEXUS's instructions on the demo account; one attempt each."""
    now = time.time() if now is None else now
    out: list[dict[str, Any]] = []
    for ins in instructions:
        guard_demo(mt5)                                   # before EVERY order
        cid = str(ins["client_id"])
        if already_sent(mt5, cid, now):
            out.append({"client_id": cid, "retcode": 0, "message": "already sent; skipped"})
            continue
        sym = str(ins["symbol"])
        info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if info is None or tick is None:
            out.append({"client_id": cid, "retcode": -1, "message": "no quote"})
            continue
        if ins["action"] == "modify":
            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym,
                   "position": int(ins["ticket"]), "sl": float(ins["sl"]),
                   "tp": float(ins.get("tp") or 0.0), "magic": MAGIC, "comment": cid}
        else:
            buy = ins["side"] == "buy"
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym,
                   "volume": float(ins["volume"]),
                   "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                   "price": float(tick.ask if buy else tick.bid),
                   "deviation": DEVIATION_POINTS, "magic": MAGIC, "comment": cid,
                   "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling(mt5, info)}
            if ins["action"] == "open":
                req.update(sl=float(ins.get("sl") or 0.0), tp=float(ins.get("tp") or 0.0))
            else:                                          # close (all or part)
                req["position"] = int(ins["ticket"])
        res = mt5.order_send(req)
        code = int(getattr(res, "retcode", -1)) if res is not None else -1
        msg = str(getattr(res, "comment", "")) if res is not None else str(mt5.last_error())
        log(f"demo {ins['action']} {sym} {ins.get('side', '')} {ins.get('volume', '')}: "
            f"retcode {code} {msg} ({ins.get('reason', '')})")
        out.append({"client_id": cid, "retcode": code, "message": msg[:200]})
    return out


def main() -> None:  # pragma: no cover - needs Windows and a running MT5 terminal
    import MetaTrader5 as mt5  # Windows only

    url, token = os.environ.get("NEXUS_URL", ""), os.environ.get("NEXUS_BRIDGE_TOKEN", "")
    if not url or not token:
        sys.exit("set NEXUS_URL and NEXUS_BRIDGE_TOKEN first (see README.md)")
    interval = max(60, int(os.environ.get("NEXUS_DEMO_INTERVAL_SECONDS", "120")))
    state: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    log(f"NEXUS MT5 demo trader {VERSION}: DEMO accounts only, symbols {', '.join(SYMBOLS)}")
    while True:
        try:
            if not mt5.initialize():
                raise RuntimeError(f"cannot attach to the MT5 terminal: {mt5.last_error()}")
            body = collect(mt5, state, results)
            resp = post(url, token, body, timeout=60, path="/api/v1/bridge/mt5/demo/sync")
            results = execute(mt5, resp.get("instructions", []))
            parts = []
            for k, v in resp.get("gates", {}).items():
                why = ", ".join(v.get("blocked_by", []))
                parts.append(f"{k}: entries allowed" if v.get("entries_allowed")
                             else f"{k}: paused ({why})")
            log("sync ok; " + "; ".join(parts))
        except NotDemo as exc:
            log(str(exc))
            sys.exit(3)
        except Exception as exc:  # keep running; NEXUS shows when it stops
            log(f"error: {type(exc).__name__}: {str(exc)[:200]}")
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
