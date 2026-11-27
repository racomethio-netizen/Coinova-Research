"""Order plans for the owner's Exness MetaTrader 5 DEMO account (virtual money only).

**Two instruments.** Neither strategy passed its pre-registered test. They run on a demo
account so the owner can watch them trade, without risking money.
* **XAUUSD (gold):** the volatility-control rule, unchanged.
  * weight = min(1, 15% / vol of the last 30 daily returns, annualised with sqrt 252);
  * it trades only when the weight is more than 10 points (of the sleeve) off target;
  * it runs in a 25% sleeve of the account, so the backtest's worst drop (-21%) stays
    near NEXUS's 5% limit.
  * Long only, never above 1x.
  * Result: failed narrowly (2026-11-27-exness-voltarget-preregistration.md).
* **EURUSD:** the NQS-1 intraday profile (H1 signals, H4 trend), unchanged.
  * one position at a time, 0.25% risk per trade;
  * a stop and target sent with the order;
  * break-even, trailing, unstable-market tightening, opposite-signal and 48-bar time
    exits are applied at each sync.
  * Result: failed narrowly (-0.01R a trade, 2026-11-27-nqs1-preregistration.md).

Pure functions. Given closed bars, the current quote, the account equity and NEXUS's own
open position, each returns instructions of these kinds:
``{"action": "open"|"close"|"modify", "symbol", "side", "volume", "sl", "tp", "ticket",
"client_id", "reason"}``

Exits and reductions are always allowed. New entries only when ``allow_entries`` is true:
the server decides that from the market switch, the kill switch and NEXUS's limits.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from nexus_strategies.nqs1.config import PROFILES, NQS1Config
from nexus_strategies.nqs1.engine import choose, prepare
from nexus_strategies.nqs1.features import resample

MAGIC = 26112701                     # marks NEXUS's orders in MT5
GOLD_SLEEVE = 0.25
GOLD_TARGET, GOLD_WINDOW, GOLD_BAND = 0.15, 30, 0.10
RISK_PER_TRADE = 0.0025
CURRENCY_CAP = 0.6
EURUSD_MIN_BARS = 900                # H4 EMA 200 needs 800 H1 bars, plus warm-up
SLIPPAGE_PIPS = 0.2
_CLIENT = re.compile(r"^nxE(\d+)r(\d+)$")


@dataclass(frozen=True)
class Spec:
    name: str                        # broker symbol, e.g. EURUSDm
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    point: float


def lots(units: float, spec: Spec) -> float:
    """Base units -> lots, rounded down to the volume step; 0 below the minimum."""
    raw = units / spec.contract_size
    steps = math.floor(raw / spec.volume_step + 1e-9)
    out = round(min(steps * spec.volume_step, spec.volume_max), 8)
    return out if out >= spec.volume_min - 1e-12 else 0.0


# ------------------------------------------------------------------------------ gold
def gold_plan(closes: pd.Series, bid: float, ask: float, equity: float,
              positions: list[dict[str, Any]], spec: Spec, allow_entries: bool,
              day: str) -> list[dict[str, Any]]:
    """``closes``: daily bid closes of CLOSED days. ``positions``: NEXUS's open XAUUSD
    positions (long only)."""
    if len(closes) < GOLD_WINDOW + 1 or equity <= 0:
        return []
    vol = float(closes.pct_change().tail(GOLD_WINDOW).std(ddof=1)) * math.sqrt(252)
    w = GOLD_SLEEVE * (1.0 if vol <= 0 else min(1.0, GOLD_TARGET / vol))
    held = sum(p["volume"] for p in positions if p["side"] == "buy")
    held_w = held * spec.contract_size * bid / equity
    if abs(w - held_w) <= GOLD_BAND * GOLD_SLEEVE:
        return []
    target = lots(w * equity / ask, spec)
    delta = round(target - held, 8)
    if delta > 0:
        if not allow_entries:
            return []
        return [{"action": "open", "symbol": spec.name, "side": "buy", "volume": delta,
                 "sl": 0.0, "tp": 0.0, "client_id": f"nxG{day}v{round(target * 100)}",
                 "reason": f"volatility rule: weight {w:.3f} of equity (vol {vol:.1%})"}]
    out: list[dict[str, Any]] = []
    need = -delta
    for p in sorted(positions, key=lambda x: -x["time"]):        # newest first
        if need <= 1e-9:
            break
        v = min(p["volume"], round(need, 8))
        out.append({"action": "close", "symbol": spec.name, "side": "sell", "volume": v,
                    "ticket": p["ticket"], "client_id": f"nxGc{day}t{p['ticket']}",
                    "reason": f"volatility rule: weight {w:.3f} (vol {vol:.1%})"})
        need -= v
    return out


# ---------------------------------------------------------------------------- eurusd
def _risk_from(position: dict[str, Any], spec: Spec) -> float | None:
    m = _CLIENT.match(position.get("comment", ""))
    if m:
        return int(m.group(2)) * spec.point
    if position.get("sl"):
        return abs(float(position["price_open"]) - float(position["sl"]))
    return None


def eurusd_plan(h1: pd.DataFrame, bid: float, ask: float, equity: float,
                position: dict[str, Any] | None, spec: Spec, allow_entries: bool,
                now: pd.Timestamp) -> list[dict[str, Any]]:
    """``h1``: CLOSED hourly bid bars (UTC open time, columns open/high/low/close).
    ``position``: NEXUS's open EURUSD position, if any."""
    if len(h1) < EURUSD_MIN_BARS or equity <= 0:
        return []
    prof, cfg = PROFILES["intraday"], NQS1Config()
    f = prepare(h1, resample(h1, "H4"), prof, cfg)
    pk = choose(f, "adaptive", cfg)
    last, pick, t = f.iloc[-1], pk.iloc[-1], f.index[-1]
    atr = float(last["atr"])
    if math.isnan(atr) or atr <= 0:
        return []
    if position is not None:
        return _manage(f, pk, position, spec, atr, prof.max_hold_bars)
    stale = now - (t + pd.Timedelta(hours=1)) > pd.Timedelta(hours=2)
    d = int(pick["dir"])
    if not allow_entries or d == 0 or stale:
        return []
    decide = t + pd.Timedelta(hours=1)
    if (decide.weekday() == 4 and decide.hour >= 19) or decide.weekday() >= 5 or \
            decide.hour >= 20:
        return []
    risk = float(pick["stop_atr"]) * atr
    spread = ask - bid
    cost = spread + 2 * SLIPPAGE_PIPS * 0.0001
    if spread > cfg.costs.max_spread_atr * atr or cost > cfg.max_cost_r * risk:
        return []
    entry = ask if d > 0 else bid
    if pick["kind"] == "mid":
        tp = float(last["bb_mid"])
        if (tp - entry) * d / risk < 1.0:
            return []
    else:
        tp = entry + d * cfg.exits.take_profit_r * risk
    units = min(RISK_PER_TRADE * equity / risk, CURRENCY_CAP * equity / entry)
    v = lots(units, spec)
    if v <= 0:
        return []
    return [{"action": "open", "symbol": spec.name, "side": "buy" if d > 0 else "sell",
             "volume": v, "sl": round(entry - d * risk, 5), "tp": round(tp, 5),
             "client_id": f"nxE{int(t.timestamp())}r{round(risk / spec.point)}",
             "reason": f"NQS-1 {pick['strategy']} in {last['regime']}, "
                       f"{int(pick['conf'])} confirmations"}]


def _manage(f: pd.DataFrame, pk: pd.DataFrame, pos: dict[str, Any], spec: Spec,
            atr: float, max_bars: int) -> list[dict[str, Any]]:
    d = 1 if pos["side"] == "buy" else -1
    entry = float(pos["price_open"])
    risk = _risk_from(pos, spec) or 1.5 * atr
    opened = pd.Timestamp(pos["time_utc"]).floor("h")
    since = f[f.index >= opened]
    best = (since["high"].max() if d > 0 else since["low"].min()) if len(since) else entry
    gain = (best - entry) * d / risk
    sl = float(pos.get("sl") or 0.0) or entry - d * risk
    new = sl
    ex = NQS1Config().exits
    if gain >= ex.breakeven_r:
        new = max(new, entry) if d > 0 else min(new, entry)
    if gain >= ex.trail_start_r:
        trail = best - d * ex.trail_atr * atr
        new = max(new, trail) if d > 0 else min(new, trail)
    last = f.iloc[-1]
    if last["regime"] == "UNSTABLE":
        tight = float(last["close"]) - d * atr
        new = max(new, tight) if d > 0 else min(new, tight)
    base = {"symbol": spec.name, "ticket": pos["ticket"], "volume": pos["volume"]}
    if int(pk.iloc[-1]["dir"]) == -d:
        return [base | {"action": "close", "side": "sell" if d > 0 else "buy",
                        "client_id": f"nxEc{pos['ticket']}", "reason": "opposite signal"}]
    if len(since) >= max_bars:
        return [base | {"action": "close", "side": "sell" if d > 0 else "buy",
                        "client_id": f"nxEc{pos['ticket']}", "reason": "time exit"}]
    if (new - sl) * d > spec.point:
        return [base | {"action": "modify", "sl": round(new, 5), "tp": pos.get("tp") or 0.0,
                        "client_id": f"nxEm{pos['ticket']}s{round(new / spec.point)}",
                        "reason": "break-even / trailing stop"}]
    return []
