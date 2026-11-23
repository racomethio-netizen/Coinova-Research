"""Trading protections modelled on Freqtrade's (freqtrade/plugins/protections), re-written
for NEXUS as pure functions over closed trades, so the backtester and the paper runner
apply exactly the same locks.

* StoplossGuard   N stop-loss exits within a lookback -> lock everything for a while.
* LowProfitPairs  a (symbol, strategy) whose recent summed return is below a floor ->
                  lock that pair for that strategy (deteriorating performance).
* MaxDrawdown     drawdown of the closed-trade equity within a lookback above X -> lock
                  everything for a while.
* Cooldown        after closing a trade, no new entry on that symbol for a while.

A lock only ever BLOCKS entries; nothing here opens, sizes up or re-enables trading.
Exits are never blocked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

STOP_KINDS = frozenset({"stop", "emergency", "emergency (no data)"})


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    strategy: str
    closed_at: datetime
    pnl: float            # money, after fees
    ret: float            # pnl / entry notional
    exit_kind: str


@dataclass(frozen=True)
class ProtectionConfig:
    stoploss_trades: int = 4
    stoploss_lookback: timedelta = timedelta(hours=24)
    stoploss_lock: timedelta = timedelta(hours=12)
    low_profit_trades: int = 4
    low_profit_lookback: timedelta = timedelta(days=21)
    low_profit_required: float = 0.0          # summed return of the pair's trades
    low_profit_lock: timedelta = timedelta(days=7)
    drawdown_lookback: timedelta = timedelta(days=7)
    drawdown_max: float = 0.03
    drawdown_min_trades: int = 3
    drawdown_lock: timedelta = timedelta(hours=48)
    cooldown: timedelta = timedelta(hours=1)


@dataclass(frozen=True)
class Lock:
    scope: str            # global | pair | symbol
    until: datetime
    reason: str
    symbol: str | None = None
    strategy: str | None = None


def _window(trades: Iterable[ClosedTrade], now: datetime, lookback: timedelta
            ) -> list[ClosedTrade]:
    return sorted((t for t in trades if now - lookback <= t.closed_at <= now),
                  key=lambda t: t.closed_at)


def stoploss_guard(trades: list[ClosedTrade], now: datetime, cfg: ProtectionConfig
                   ) -> Lock | None:
    hits = [t for t in _window(trades, now, cfg.stoploss_lookback)
            if t.exit_kind in STOP_KINDS and t.pnl < 0]
    if len(hits) < cfg.stoploss_trades:
        return None
    until = hits[-1].closed_at + cfg.stoploss_lock
    return Lock("global", until, f"{len(hits)} stop-losses within "
                f"{cfg.stoploss_lookback}") if until > now else None


def low_profit_pairs(trades: list[ClosedTrade], now: datetime, cfg: ProtectionConfig
                     ) -> list[Lock]:
    by_pair: dict[tuple[str, str], list[ClosedTrade]] = {}
    for t in _window(trades, now, cfg.low_profit_lookback):
        by_pair.setdefault((t.symbol, t.strategy), []).append(t)
    out = []
    for (sym, strat), ts in by_pair.items():
        total = sum(t.ret for t in ts)
        if len(ts) >= cfg.low_profit_trades and total < cfg.low_profit_required:
            until = ts[-1].closed_at + cfg.low_profit_lock
            if until > now:
                out.append(Lock("pair", until, f"{strat} on {sym}: {len(ts)} trades returned "
                                f"{total:+.2%} in {cfg.low_profit_lookback}", sym, strat))
    return out


def max_drawdown(trades: list[ClosedTrade], now: datetime, cfg: ProtectionConfig,
                 equity_at_window_start: float) -> Lock | None:
    ts = _window(trades, now, cfg.drawdown_lookback)
    if len(ts) < cfg.drawdown_min_trades or equity_at_window_start <= 0:
        return None
    eq = peak = equity_at_window_start
    worst = 0.0
    for t in ts:
        eq += t.pnl
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    if worst <= cfg.drawdown_max:
        return None
    until = ts[-1].closed_at + cfg.drawdown_lock
    return Lock("global", until, f"closed-trade drawdown {worst:.2%} above "
                f"{cfg.drawdown_max:.0%} within {cfg.drawdown_lookback}") if until > now else None


def cooldowns(trades: list[ClosedTrade], now: datetime, cfg: ProtectionConfig) -> list[Lock]:
    last: dict[str, datetime] = {}
    for t in trades:
        if t.closed_at <= now:
            last[t.symbol] = max(last.get(t.symbol, t.closed_at), t.closed_at)
    return [Lock("symbol", at + cfg.cooldown, f"cooldown after closing {sym}", sym)
            for sym, at in last.items() if at + cfg.cooldown > now]


def evaluate(trades: list[ClosedTrade], now: datetime, cfg: ProtectionConfig, *,
             equity_now: float) -> list[Lock]:
    """All active locks. ``equity_now`` reconstructs the equity at the drawdown window's
    start (current equity minus the P&L closed inside the window)."""
    locks: list[Lock] = []
    g = stoploss_guard(trades, now, cfg)
    if g:
        locks.append(g)
    window_pnl = sum(t.pnl for t in _window(trades, now, cfg.drawdown_lookback))
    d = max_drawdown(trades, now, cfg, equity_now - window_pnl)
    if d:
        locks.append(d)
    locks.extend(low_profit_pairs(trades, now, cfg))
    locks.extend(cooldowns(trades, now, cfg))
    return locks


def blocking(locks: list[Lock], symbol: str, strategy: str | None) -> list[str]:
    """Reasons the locks refuse an entry on ``symbol`` by ``strategy`` (empty = allowed)."""
    out = []
    for lk in locks:
        if lk.scope == "global" or (lk.scope == "symbol" and lk.symbol == symbol) or (
                lk.scope == "pair" and lk.symbol == symbol and lk.strategy == strategy):
            out.append(f"protection: {lk.reason} (until {lk.until:%Y-%m-%d %H:%M} UTC)")
    return out
