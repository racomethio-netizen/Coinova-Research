"""Autonomous adaptive grid system, PAPER ONLY.

The loop (every ``TICK_SECONDS``):
1. **Owner commands** (start, pause, resume, stop all, emergency shutdown, risk limits).
   They come from the API (owner-only, audited) through a Redis list.
2. **Market data.** Book tickers for spot and futures, futures premium index (mark price
   and funding). Every request goes through the central Binance weight gate. A failed or
   stale feed means no fills and no new orders on that market.
3. **Universe** (hourly) and **regime** (every 15 minutes, closed 1h bars) refresh.
4. **Portfolio risk state** over both paper accounts (``GridRiskManager``):
   * ``SHUTDOWN`` closes everything and latches;
   * ``NO_NEW_ORDERS`` suspends entries.
5. **Orchestrator decision per symbol.**
   * ``ADAPTIVE_GRID`` (long, short or neutral) when the regime allows it.
   * ``TREND_FOLLOWING`` in a strong trend. That belongs to the separate NEXUS trend
     engines and their own paper ledgers, so the grid engine stands aside and capital
     never overlaps.
   * ``SCALPING``: never selected. No scalping strategy has passed validation (the
     Freqtrade community test showed fast strategies losing to fees).
   * ``NO_TRADE`` otherwise.
6. **Grid lifecycle.**
   * Design with an allocation from the allocator.
   * Fill simulated orders on the live book.
   * Arm new entries, each authorised by the risk manager.
   * Suspend when the regime turns; resume when it returns.
   * Rebuild only when flat and after the cool-down.
   * A stop-loss latches until the owner restarts.
7. **Persistence.** All of a tick's events, and a full ``snapshot`` whenever state
   changed, are written to the append-only ``adaptive_grid_events`` in ONE transaction.
   If that write fails, the in-memory state is reloaded from the last committed snapshot,
   so a simulated fill is never applied twice and a restart resumes exactly.

There is NO live path: this module only issues public market-data GET requests. It never
holds API keys, never builds an exchange order client, and its mode cannot be switched to
live. See tests/unit/test_adaptive_grid_paper.py::test_no_live_order_path.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from nexus_common.logging import get_logger
from nexus_risk.grid_risk import (
    GridRiskLimits,
    GridRiskManager,
    OrderIntent,
    PortfolioAllocator,
    PortfolioView,
    period_starts,
)
from nexus_strategies import adaptive_grid as ag
from nexus_strategies import grid_regime as gr

log = get_logger("nexus.agrid")

D = Decimal
ZERO = D(0)
TICK_SECONDS = 10
UNIVERSE_EVERY = 3600
REGIME_EVERY = 900
PREMIUM_EVERY = 60
SNAPSHOT_EVERY = 300
STATUS_KEY = "nexus:agrid:status"
COMMANDS_KEY = "nexus:agrid:commands"
LIMITS_KEY = "nexus:agrid:limits"
HEARTBEAT = "agrid:paper"
MARKETS = ("spot", "futures")
STATEFUL = frozenset({"fill", "funding", "grid_start", "retire", "stop", "control",
                      "suspend", "resume"})


class DataSource(Protocol):
    async def books(self, market: str, symbols: list[str]) -> dict[str, ag.Book] | None: ...
    async def scan(self, market: str, incumbents: list[str]) -> dict[str, Any]: ...
    async def klines(self, market: str, symbol: str) -> Any: ...
    async def premium(self) -> dict[str, dict[str, Any]] | None: ...
    async def commands(self) -> list[dict[str, Any]]: ...
    async def kill_switch(self) -> bool | None: ...


class Ledger(Protocol):
    def write(self, events: list[dict[str, Any]]) -> None: ...
    def scan(self, market: str, at: datetime, data: dict[str, Any]) -> None: ...
    def latest_snapshot(self) -> tuple[int, dict[str, Any]] | None: ...
    def max_seq(self) -> int: ...


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


@dataclass
class Account:
    capital: Decimal
    cash: Decimal
    grids: dict[str, ag.GridState] = field(default_factory=dict)


class AdaptiveGridSystem:
    def __init__(self, data: DataSource, ledger: Ledger, *, spot_capital: Decimal,
                 futures_capital: Decimal, limits: GridRiskLimits | None = None,
                 params: ag.GridParams | None = None) -> None:
        self.data, self.ledger = data, ledger
        self.limits = limits or GridRiskLimits()
        self.params = params or ag.GridParams(max_order_risk=self.limits.max_order_risk)
        self.accounts = {"spot": Account(spot_capital, spot_capital),
                         "futures": Account(futures_capital, futures_capital)}
        self.peak = spot_capital + futures_capital
        self.day: tuple[str, Decimal] = ("", self.peak)
        self.week: tuple[str, Decimal] = ("", self.peak)
        self.universe: dict[str, list[str]] = {"spot": [], "futures": []}
        self.filters: dict[str, dict[str, Any]] = {"spot": {}, "futures": {}}
        self.regimes: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.refusals: dict[str, str] = {}
        self.last_rejections: dict[str, str] = {}
        self.control: dict[str, Any] = {"status": "RUNNING", "paused": [], "shutdown": False}
        self.funding: dict[str, dict[str, Any]] = {}
        self.marks: dict[str, Decimal] = {}
        self.btc_corr: dict[str, float] = {}
        self.risk_state: tuple[str, list[str]] = ("NORMAL", [])
        self.seq = 0
        self.last = {"universe": 0.0, "regime": 0.0, "premium": 0.0, "snapshot": 0.0}
        self.data_ok = {"spot": False, "futures": False}
        self.recent: list[dict[str, Any]] = []
        self.buffer: list[dict[str, Any]] = []
        self.state_changed = False

    # ---------------------------------------------------------------- persistence
    def _event(self, now: float, account: str, kind: str, data: dict[str, Any], *,
               grid: ag.GridState | None = None, symbol: str | None = None) -> None:
        """Buffer an event; the whole tick is written atomically by ``flush``."""
        self.seq += 1
        at = datetime.fromtimestamp(now, UTC)
        sym = symbol or (grid.symbol if grid else None)
        self.buffer.append({"seq": self.seq, "at": at, "account": account,
                            "grid_id": grid.grid_id if grid else None, "symbol": sym,
                            "kind": kind, "data": jsonable(data)})
        if kind in STATEFUL:
            self.state_changed = True
        if kind != "snapshot":
            self.recent = [{"at": at.isoformat(), "account": account, "kind": kind,
                            "symbol": sym, **{k: v for k, v in data.items() if k != "state"}},
                           *self.recent][:80]

    def flush(self) -> None:
        """Write the tick's events (and snapshot) in ONE transaction. On failure, reload
        the last committed snapshot so no simulated fill is ever applied twice."""
        events, self.buffer = self.buffer, []
        if not events:
            return
        try:
            self.ledger.write(events)
        except Exception:
            self.restore()
            raise

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = jsonable({
            "accounts": {m: {"capital": a.capital, "cash": a.cash,
                             "grids": {s: g.to_json() for s, g in a.grids.items()}}
                         for m, a in self.accounts.items()},
            "peak": self.peak, "day": list(self.day), "week": list(self.week),
            "universe": self.universe, "filters": self.filters, "regimes": self.regimes,
            "decisions": self.decisions, "control": self.control, "funding": self.funding,
            "limits": self.limits.to_json(), "last": self.last, "marks": self.marks})
        return out

    def restore(self) -> bool:
        self.buffer = []
        snap = self.ledger.latest_snapshot()
        self.seq = self.ledger.max_seq()
        if snap is None:
            return False
        _, d = snap
        for m, a in d["accounts"].items():
            self.accounts[m] = Account(D(a["capital"]), D(a["cash"]),
                                       {s: ag.GridState.from_json(g)
                                        for s, g in a["grids"].items()})
        self.peak = D(d["peak"])
        self.day, self.week = (d["day"][0], D(d["day"][1])), (d["week"][0], D(d["week"][1]))
        self.universe, self.filters = d["universe"], d["filters"]
        self.regimes, self.decisions = d["regimes"], d["decisions"]
        self.control, self.funding = d["control"], d.get("funding", {})
        self.limits = GridRiskLimits.from_json(d["limits"])
        self.marks = {k: D(v) for k, v in d.get("marks", {}).items()}
        self.last.update({k: float(v) for k, v in d.get("last", {}).items()})
        return True

    # -------------------------------------------------------------------- views
    def _equity(self, market: str) -> Decimal:
        a = self.accounts[market]
        eq = a.cash
        for s, g in a.grids.items():
            m = self.marks.get(f"{market}:{s}")
            eq += g.equity(m, m) if m is not None else g.quote
        return eq

    def view(self, kill: bool | None, floor: str | None = None) -> PortfolioView:
        exposure: dict[str, Decimal] = {}
        orders, margin = 0, ZERO
        for market, a in self.accounts.items():
            for s, g in a.grids.items():
                m = self.marks.get(f"{market}:{s}") or g.anchor
                asset = s[:-4] if s.endswith("USDT") else s
                notional = (g.long_qty + g.short_qty) * m + sum(
                    (sl.remaining * sl.entry_price for sl in g.slots if sl.armed), ZERO)
                exposure[asset] = exposure.get(asset, ZERO) + notional
                orders += len(g.open_orders())
                if market == "futures":
                    margin += g.position_margin() + g.reserved()
        active = [m for m in MARKETS if self.accounts[m].grids]
        return PortfolioView(
            equity=self._equity("spot") + self._equity("futures"), peak_equity=self.peak,
            day_start_equity=self.day[1], week_start_equity=self.week[1],
            exposure_by_asset=exposure, futures_margin=margin,
            futures_equity=self._equity("futures"), open_orders=orders,
            btc_corr=dict(self.btc_corr),
            data_ok=all(self.data_ok[m] for m in active), kill_switch=kill,
            owner_floor=floor, latched_shutdown=bool(self.control.get("shutdown")))

    # ----------------------------------------------------------------- commands
    def apply_command(self, cmd: dict[str, Any], now: float) -> None:
        action, c = cmd.get("action"), self.control
        if action == "start":
            for a in self.accounts.values():     # retire stopped and flat grids
                for s in [s for s, g in a.grids.items() if g.status == "STOPPED"
                          or (c["status"] != "RUNNING" and g.long_qty == 0
                              and g.short_qty == 0)]:
                    a.cash += a.grids.pop(s).quote
            if c.get("shutdown"):
                self.peak = self._equity("spot") + self._equity("futures")
            c.update(status="RUNNING", shutdown=False, paused=[])
        elif action == "pause":
            if cmd.get("symbol"):
                c["paused"] = sorted(set(c["paused"]) | {cmd["symbol"]})
            elif c["status"] == "RUNNING":
                c["status"] = "PAUSED"
        elif action == "resume":
            if cmd.get("symbol"):
                c["paused"] = [x for x in c["paused"] if x != cmd["symbol"]]
            elif c["status"] == "PAUSED":
                c["status"] = "RUNNING"
        elif action == "stop_all":
            c["status"] = "STOPPED"
        elif action == "emergency_shutdown":
            c.update(status="STOPPED", shutdown=True)
        elif action == "set_limits":
            lim = GridRiskLimits.from_json(cmd.get("limits", {}))
            if lim.problems():
                cmd = {**cmd, "refused": lim.problems()}
            else:
                self.limits = lim
        else:
            cmd = {**cmd, "refused": ["unknown command"]}
        self._event(now, "system", "control", {"command": cmd, "control": dict(c)})

    # ------------------------------------------------------------------- refresh
    async def refresh_universe(self, now: float) -> None:
        for market in MARKETS:
            active = list(self.accounts[market].grids)
            try:
                res = await self.data.scan(market, self.universe[market])
            except Exception as exc:
                self._event(now, "system", "gap", {
                    "what": f"{market} universe scan",
                    "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            self.universe[market] = list(res["selected"])
            self.filters[market] = {**{s: f for s, f in self.filters[market].items()
                                       if s in active}, **res.get("filters", {})}
            self.ledger.scan(market, datetime.fromtimestamp(now, UTC), jsonable(res))
        self.last["universe"] = now

    async def refresh_regimes(self, now: float) -> None:
        import numpy as np

        closes: dict[str, Any] = {}
        for market in MARKETS:
            for sym in sorted(set(self.universe[market]) | set(self.accounts[market].grids)):
                key = f"{market}:{sym}"
                try:
                    df = await self.data.klines(market, sym)
                except Exception as exc:
                    self.regimes[key] = {"regime": "DATA_UNAVAILABLE", "grid": None,
                                         "start_ok": False, "reasons": [str(exc)[:200]]}
                    continue
                r = gr.latest(df, market)
                if r.get("regime") != self.regimes.get(key, {}).get("regime"):
                    self._event(now, market, "regime", {k: r[k] for k in (
                        "regime", "grid", "start_ok", "reasons")}, symbol=sym)
                self.regimes[key] = r
                if sym not in closes:
                    closes[sym] = df["close"].astype(float).tail(169)
        btc = closes.get("BTCUSDT")
        if btc is not None:
            rb = np.log(btc).diff().dropna()
            for sym, c in closes.items():
                rs = np.log(c).diff().dropna()
                n = min(len(rs), len(rb))
                if n > 48:
                    v = float(np.corrcoef(rs.tail(n), rb.tail(n))[0, 1])
                    self.btc_corr[sym[:-4]] = v if math.isfinite(v) else 1.0
        self.last["regime"] = now

    async def refresh_premium(self, now: float) -> None:
        prem = await self.data.premium()
        self.last["premium"] = now
        if prem is None:
            return
        for sym, g in self.accounts["futures"].grids.items():
            p = prem.get(sym)
            if p is None:
                continue
            f = self.funding.setdefault(sym, {"settled_ms": 0})
            due = f.get("next_ms")
            if due and now * 1000 >= due and f["settled_ms"] < due:
                paid = ag.apply_funding(g, D(str(f["rate"])), D(str(p["mark"])))
                f["settled_ms"] = due
                self._event(now, "futures", "funding", {"rate": f["rate"], "mark": p["mark"],
                                                        "paid": str(paid)}, grid=g)
            f.update(next_ms=int(p["next_funding_ms"]), rate=p["rate"])
            self.marks[f"futures:{sym}"] = D(str(p["mark"]))

    # ---------------------------------------------------------------- decisions
    def decide(self, market: str, sym: str) -> dict[str, Any]:
        r = self.regimes.get(f"{market}:{sym}", {"regime": "UNKNOWN", "grid": None,
                                                 "start_ok": False, "reasons": []})
        regime, mode = r.get("regime"), r.get("grid")
        why = list(r.get("reasons", []))
        if mode:
            strategy = "ADAPTIVE_GRID"
        elif regime in ("STRONG_BULL", "STRONG_BEAR"):
            strategy = "TREND_FOLLOWING"
            why.append("strong trend: handled by the NEXUS trend engines (separate paper "
                       "ledgers); the grid engine stands aside")
        else:
            strategy = "NO_TRADE"
        if sym not in self.universe[market]:
            strategy, mode = "NO_TRADE", None
            why.append("not in the current trading universe")
        return {"strategy": strategy, "mode": mode if strategy == "ADAPTIVE_GRID" else None,
                "regime": regime, "start_ok": bool(r.get("start_ok")), "reasons": why}

    def _authorizer(self, market: str, view: PortfolioView) -> ag.Authorizer:
        risk = GridRiskManager(self.limits)

        def authorize(st: ag.GridState, slot: ag.Slot) -> tuple[bool, list[str]]:
            f = st.fees
            if slot.side == "long" and st.stop_low is not None:
                px = st.stop_low * (1 - f["slippage"])
                loss = slot.qty * (slot.entry_price - px) + slot.qty * (
                    slot.entry_price * f["maker"] + px * f["taker"])
            elif slot.side == "short" and st.stop_high is not None:
                px = st.stop_high * (1 + f["slippage"])
                loss = slot.qty * (px - slot.entry_price) + slot.qty * (
                    slot.entry_price * f["maker"] + px * f["taker"])
            else:
                return False, ["no stop defined for this slot"]
            notional = slot.qty * slot.entry_price
            margin = notional / st.params.leverage if market == "futures" else ZERO
            intent = OrderIntent(market, st.symbol, st.symbol[:-4],
                                 "buy" if slot.side == "long" else "sell", slot.entry_price,
                                 slot.qty, loss, margin)
            ok, why = risk.authorize(intent, view)
            if ok:          # later approvals in the same tick see this order's exposure
                view.exposure_by_asset[intent.asset] = view.exposure_by_asset.get(
                    intent.asset, ZERO) + notional
                view.open_orders += 1
                view.futures_margin += margin
            return ok, why
        return authorize

    # --------------------------------------------------------------------- tick
    async def tick(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        self.state_changed = False
        for cmd in await self.data.commands():
            self.apply_command(cmd, now)
        books: dict[str, dict[str, ag.Book]] = {}
        for market in MARKETS:
            syms = sorted(set(self.universe[market]) | set(self.accounts[market].grids))
            got = await self.data.books(market, syms) if syms else {}
            self.data_ok[market] = got is not None
            books[market] = got or {}
            for s, b in books[market].items():
                if market == "spot" or f"futures:{s}" not in self.marks:
                    self.marks[f"{market}:{s}"] = (b.bid + b.ask) / 2
        if now - self.last["universe"] >= UNIVERSE_EVERY:
            await self.refresh_universe(now)
        if now - self.last["regime"] >= REGIME_EVERY:
            await self.refresh_regimes(now)
        if self.accounts["futures"].grids and now - self.last["premium"] >= PREMIUM_EVERY:
            await self.refresh_premium(now)
        kill = await self.data.kill_switch()
        eq = self._equity("spot") + self._equity("futures")
        self.peak = max(self.peak, eq)
        dk, wk = period_starts(datetime.fromtimestamp(now, UTC))
        if self.day[0] != dk:
            self.day = (dk, eq)
        if self.week[0] != wk:
            self.week = (wk, eq)
        view = self.view(kill)
        state = GridRiskManager(self.limits).portfolio_state(view)
        if state != self.risk_state:
            self._event(now, "system", "risk", {"state": state[0], "reasons": state[1]})
            self.risk_state = state
        if state[0] == "SHUTDOWN" and not self.control.get("shutdown"):
            self.control.update(status="STOPPED", shutdown=True)
            self._event(now, "system", "control", {"command": {"action": "risk_shutdown"},
                                                   "control": dict(self.control)})
        for market in MARKETS:
            self._market_tick(market, books[market], now, view, state[0])
        if self.state_changed or now - self.last["snapshot"] >= SNAPSHOT_EVERY:
            self.last["snapshot"] = now
            self._event(now, "system", "snapshot", {"state": self.to_json()})
        self.flush()
        return self.status(now, kill)

    def _market_tick(self, market: str, books: dict[str, ag.Book], now: float,
                     view: PortfolioView, risk_state: str) -> None:
        acct = self.accounts[market]
        halt = self.control["status"] != "RUNNING" or bool(self.control.get("shutdown"))
        for sym in sorted(set(self.universe[market]) | set(acct.grids)):
            dec = self.decide(market, sym)
            key = f"{market}:{sym}"
            old = self.decisions.get(key, {})
            if any(dec[k] != old.get(k) for k in ("strategy", "mode", "regime")):
                self._event(now, market, "decision", dec, symbol=sym)
            self.decisions[key] = dec
            book = books.get(sym)
            g = acct.grids.get(sym)
            if g is not None:
                g = self._grid_tick(market, acct, sym, g, book, dec, now, view, risk_state)
            if g is None and not halt and risk_state == "NORMAL" and book is not None \
                    and dec["mode"] and dec["start_ok"] and sym not in self.control["paused"]:
                self._start_grid(market, sym, dec, book, now, view)

    def _grid_tick(self, market: str, acct: Account, sym: str, g: ag.GridState,
                   book: ag.Book | None, dec: dict[str, Any], now: float,
                   view: PortfolioView, risk_state: str) -> ag.GridState | None:
        if book is None:
            return g                             # stale data: no fills, no new orders
        status = self.control["status"]
        if status == "STOPPED":                  # stop all / emergency / risk shutdown
            if g.status != "STOPPED" or g.long_qty or g.short_qty:
                reason = ("emergency shutdown" if self.control.get("shutdown")
                          else "owner stopped all strategies")
                self._log_fills(market, g, ag.close_all(g, book, reason), now)
                self._event(now, market, "stop", {"reason": reason}, grid=g)
            return g
        fills = ag.step(g, book, now)
        self._log_fills(market, g, fills, now)
        if g.status == "STOPPED":
            if fills:
                self._event(now, market, "stop", {"reason": g.stop_reason}, grid=g)
            return g
        regime_off = dec["mode"] is None or dec["mode"] != g.mode
        blocked = status != "RUNNING" or sym in self.control["paused"] or \
            risk_state != "NORMAL" or regime_off
        if blocked and g.status == "RUNNING":
            n = ag.disarm(g)
            g.status = "SUSPENDED"
            why = dec["reasons"] if regime_off else [
                f"risk {risk_state}" if risk_state != "NORMAL" else "paused by the owner"]
            self._event(now, market, "suspend", {"cancelled_entries": n, "reasons": why},
                        grid=g)
        elif not blocked and g.status == "SUSPENDED" and dec["start_ok"]:
            g.status = "RUNNING"
            self._event(now, market, "resume", {"regime": dec["regime"]}, grid=g)
        why_rebuild = ag.should_rebuild(g, now, dec["mode"])
        if why_rebuild and (g.status != "RUNNING" or g.out_of_range_since):
            acct.cash += g.quote
            del acct.grids[sym]
            self._event(now, market, "retire", {"reason": why_rebuild,
                                                "final": ag.summary(g, book)}, grid=g)
            return None
        if g.status == "RUNNING":
            decisions = ag.arm(g, book, self._authorizer(market, view))
            ok = [d for d in decisions if d["approved"]]
            if ok:
                self._event(now, market, "arm", {"orders": ok}, grid=g)
            rejected = sorted({r for d in decisions if not d["approved"] for r in d["reasons"]})
            if rejected and self.last_rejections.get(g.grid_id) != str(rejected):
                self.last_rejections[g.grid_id] = str(rejected)
                self._event(now, market, "risk", {
                    "rejected_orders": sum(1 for d in decisions if not d["approved"]),
                    "reasons": rejected}, grid=g)
        return g

    def _start_grid(self, market: str, sym: str, dec: dict[str, Any], book: ag.Book,
                    now: float, view: PortfolioView) -> None:
        acct = self.accounts[market]
        f = self.filters[market].get(sym)
        r = self.regimes.get(f"{market}:{sym}", {})
        if f is None or not r.get("atr_pct"):
            return
        imb = gr.book_imbalance(float(book.bid_qty), float(book.ask_qty), float(book.bid),
                                float(book.ask))
        if imb is not None and abs(imb) > gr.GridRegimeConfig().max_imbalance:
            return                               # one-sided book: wait for the next tick
        slots = max(len(self.universe[market]), 1)
        plan = PortfolioAllocator(self.limits).plan({market: acct.capital}, view.equity,
                                                    {market: [sym]})
        alloc = min(plan.per_grid[f"{market}:{sym}"], acct.capital / slots, acct.cash)
        mid = (book.bid + book.ask) / 2
        inp = ag.DesignInput(
            market, sym, dec["mode"], mid, D(str(r["atr_pct"])),
            D(str(r["support"])) if r.get("support") else None,
            D(str(r["resistance"])) if r.get("resistance") else None, alloc, view.equity,
            ag.Filters(D(str(f["price_tick"])), D(str(f["amount_step"])),
                       D(str(f["min_qty"])), D(str(f["min_notional"]))), now)
        st, rep = ag.design(inp, self.params, grid_id=f"{market}-{sym}-{int(now)}")
        if st is None:
            why = "; ".join(rep["reasons"])
            if self.refusals.get(f"{market}:{sym}") != why:
                self.refusals[f"{market}:{sym}"] = why
                self._event(now, market, "grid_refused", {"report": rep}, symbol=sym)
            return
        acct.cash -= alloc
        acct.grids[sym] = st
        self.refusals.pop(f"{market}:{sym}", None)
        self._event(now, market, "grid_start", {
            "design": rep, "allocation": str(alloc), "regime": dec["regime"],
            "mode": dec["mode"], "levels": [str(x) for x in st.levels]}, grid=st)
        decisions = ag.arm(st, book, self._authorizer(market, view))
        ok = [d for d in decisions if d["approved"]]
        if ok:
            self._event(now, market, "arm", {"orders": ok}, grid=st)

    def _log_fills(self, market: str, g: ag.GridState, fills: list[dict[str, Any]],
                   now: float) -> None:
        for f in fills:
            self._event(now, market, "fill", f, grid=g)

    def status(self, now: float, kill: bool | None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "paper": True, "updated_at": datetime.fromtimestamp(now, UTC).isoformat(),
            "control": self.control, "kill_switch": kill,
            "risk_state": {"state": self.risk_state[0], "reasons": self.risk_state[1]},
            "limits": self.limits.to_json(), "data_ok": self.data_ok,
            "universe": self.universe,
            "regimes": {k: {x: v.get(x) for x in ("regime", "grid", "start_ok", "reasons",
                                                   "adx", "atr_pct", "rsi")}
                        for k, v in self.regimes.items()},
            "decisions": self.decisions, "recent": self.recent[:40],
            "peak_equity": str(self.peak), "day_start": str(self.day[1]),
            "week_start": str(self.week[1]), "accounts": {}}
        total = ZERO
        for market, a in self.accounts.items():
            grids = []
            for s, g in a.grids.items():
                m = self.marks.get(f"{market}:{s}") or g.anchor
                grids.append({**ag.summary(g, ag.Book(m, m, ZERO, ZERO)),
                              "orders": g.open_orders(),
                              "levels_list": [str(x) for x in g.levels]})
            eq = self._equity(market)
            total += eq
            out["accounts"][market] = {"capital": str(a.capital), "cash": str(a.cash),
                                       "equity": str(eq), "pnl": str(eq - a.capital),
                                       "grids": grids}
        cap = sum((a.capital for a in self.accounts.values()), ZERO)
        out["equity"], out["pnl"] = str(total), str(total - cap)
        out["drawdown"] = str(1 - total / self.peak) if self.peak > 0 else "0"
        return out


# ----------------------------------------------------------------- production I/O
class BinanceData:
    """Public market data only (no keys, no order endpoints), through the weight gate."""

    SPOT = "https://api.binance.com/api/v3"
    FAPI = "https://fapi.binance.com/fapi/v1"

    def __init__(self, client: Any, redis: Any) -> None:
        self.client, self.redis = client, redis

    async def _get(self, url: str, params: dict[str, Any], ttl: int) -> Any:
        from nexus_common.binance_guard import guarded_get

        r = await guarded_get(self.client, self.redis, url, params, cache_ttl=ttl)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    async def books(self, market: str, symbols: list[str]) -> dict[str, ag.Book] | None:
        try:
            if market == "spot":
                rows = await self._get(f"{self.SPOT}/ticker/bookTicker", {
                    "symbols": json.dumps(symbols, separators=(",", ":"))}, 5)
            else:
                rows = await self._get(f"{self.FAPI}/ticker/bookTicker", {}, 5)
        except Exception:
            return None
        want = set(symbols)
        return {r["symbol"]: ag.Book(D(r["bidPrice"]), D(r["askPrice"]), D(r["bidQty"]),
                                     D(r["askQty"]))
                for r in rows if r["symbol"] in want and D(r["bidPrice"]) > 0}

    async def scan(self, market: str, incumbents: list[str]) -> dict[str, Any]:
        from nexus_market.universe import FUTURES_RULES, SPOT_RULES, scan_market

        return await scan_market(self.client, self.redis,
                                 SPOT_RULES if market == "spot" else FUTURES_RULES,
                                 incumbents, int(time.time() * 1000))

    async def klines(self, market: str, symbol: str) -> Any:
        import pandas as pd

        url = f"{self.SPOT}/klines" if market == "spot" else f"{self.FAPI}/klines"
        rows = await self._get(url, {"symbol": symbol, "interval": "1h", "limit": 300}, 900)
        rows = rows[:-1]                                  # the last bar may be open
        idx = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
        return pd.DataFrame({"open": [float(r[1]) for r in rows],
                             "high": [float(r[2]) for r in rows],
                             "low": [float(r[3]) for r in rows],
                             "close": [float(r[4]) for r in rows],
                             "volume": [float(r[5]) for r in rows]}, index=idx)

    async def premium(self) -> dict[str, dict[str, Any]] | None:
        try:
            rows = await self._get(f"{self.FAPI}/premiumIndex", {}, 30)
        except Exception:
            return None
        return {r["symbol"]: {"mark": r["markPrice"], "rate": r["lastFundingRate"],
                              "next_funding_ms": r["nextFundingTime"]} for r in rows}

    async def commands(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            raw = await self.redis.lpop(COMMANDS_KEY)
            if raw is None:
                return out
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue

    async def kill_switch(self) -> bool | None:
        from nexus_risk.kill_switch import KillSwitch

        try:
            return await KillSwitch(self.redis).status() is not None
        except Exception:
            return None


class DbLedger:
    def __init__(self, factory: Any) -> None:
        self.factory = factory

    def write(self, events: list[dict[str, Any]]) -> None:
        """All events of one tick in a single transaction (all or nothing)."""
        from nexus_db import models, transaction

        with transaction(self.factory) as s:
            for e in events:
                s.add(models.AdaptiveGridEvent(**e))

    def scan(self, market: str, at: datetime, data: dict[str, Any]) -> None:
        from nexus_db import models, transaction

        with transaction(self.factory) as s:
            s.add(models.GridUniverseScan(market=market, at=at, data=data))

    def latest_snapshot(self) -> tuple[int, dict[str, Any]] | None:
        from nexus_db import models, transaction
        from sqlalchemy import select

        with transaction(self.factory) as s:
            row = s.scalar(select(models.AdaptiveGridEvent)
                           .where(models.AdaptiveGridEvent.kind == "snapshot")
                           .order_by(models.AdaptiveGridEvent.id.desc()).limit(1))
            return None if row is None else (row.seq, dict(row.data["state"]))

    def max_seq(self) -> int:
        from nexus_db import models, transaction
        from sqlalchemy import func, select

        with transaction(self.factory) as s:
            return int(s.scalar(select(func.coalesce(func.max(
                models.AdaptiveGridEvent.seq), 0))) or 0)


async def serve(settings: Any, stop: asyncio.Event) -> None:
    import httpx
    from nexus_common.proxy import httpx_kwargs
    from nexus_common.redisutil import connect as connect_redis
    from nexus_db import make_engine, make_session_factory

    if settings.agrid_mode == "off":
        log.info("agrid_not_started", reason="NEXUS_AGRID_MODE=off")
        return
    engine = make_engine(settings.database_url.get_secret_value(), pooled=False)
    factory = make_session_factory(engine)
    redis = connect_redis(settings.redis_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=True, **httpx_kwargs()) as client:
            limits = GridRiskLimits()
            raw = await redis.get(LIMITS_KEY)
            if raw:
                lim = GridRiskLimits.from_json(json.loads(raw))
                limits = lim if not lim.problems() else limits
            system = AdaptiveGridSystem(
                BinanceData(client, redis), DbLedger(factory),
                spot_capital=D(str(settings.agrid_spot_capital)),
                futures_capital=D(str(settings.agrid_futures_capital)), limits=limits)
            resumed = await asyncio.to_thread(system.restore)
            log.info("agrid_started", resumed=resumed, paper=True)
            while not stop.is_set():
                try:
                    st = await system.tick()
                    await redis.set(STATUS_KEY, json.dumps(st, default=str), ex=600)
                    await redis.set(f"nexus:heartbeat:{HEARTBEAT}",
                                    datetime.now(UTC).isoformat(), ex=600)
                except Exception as exc:          # one bad tick must not stop the system
                    log.error("agrid_tick_failed",
                              error=f"{type(exc).__name__}: {exc}"[:300])
                try:
                    await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
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
